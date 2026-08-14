#!/usr/bin/env bash
# Deploy the single supported minimal-pool architecture to the ethrex testnet.
# Required: RPC_URL, DEPLOYER_PK. The repository key is a single-party testbed
# setup, so public-testnet use must explicitly set ALLOW_TESTBED_SETUP=1.
set -euo pipefail
cd "$(dirname "$0")"

RPC=${RPC_URL:?set RPC_URL}
: "${DEPLOYER_PK:?set DEPLOYER_PK}"
[[ ${ALLOW_TESTBED_SETUP:-0} == 1 ]] || {
  echo "refusing deployment: set ALLOW_TESTBED_SETUP=1 for the disposable testnet key" >&2
  echo "a production deployment requires a separately verified multi-party zkey" >&2
  exit 1
}
python3 ../tooling/check_activation.py ../activation_manifest.testbed.json --allow-testbed

BN=../contracts
PRICE=(--gas-price 3000000000 --priority-gas-price 1000000000)
SMOKE_OUTPUT=${SMOKE_OUTPUT:-../wallet/artifacts/smoke_fixture.live.json}
deployed() { grep -oE 'Deployed to: 0x[0-9a-fA-F]{40}' | awk '{print $3}'; }
addr_of() { python3 -c 'import json,sys; print(json.load(sys.stdin)["contractAddress"])'; }
verify_library_runtime() {
  local addr=$1 expected=$2 actual prefix actual_lower prefix_lower
  actual=$(cast code "$addr" --rpc-url "$RPC")
  prefix="0x73${addr#0x}"
  actual_lower=$(printf '%s' "$actual" | tr '[:upper:]' '[:lower:]')
  prefix_lower=$(printf '%s' "$prefix" | tr '[:upper:]' '[:lower:]')
  [[ $actual_lower == "$prefix_lower"* && ${actual:44} == ${expected:44} ]]
}
verify_logic_runtime() {
  local addr=$1 expected=$2 actual
  actual=$(cast code "$addr" --rpc-url "$RPC")
  python3 - "$actual" "$expected" "$BN/out/ShieldedPoolLogic.sol/ShieldedPoolLogic.json" <<'PY'
import json, sys
actual = bytearray.fromhex(sys.argv[1][2:])
expected = bytearray.fromhex(sys.argv[2][2:])
refs = json.load(open(sys.argv[3]))["deployedBytecode"]["immutableReferences"]
if len(actual) != len(expected):
    raise SystemExit(1)
for locations in refs.values():
    for item in locations:
        start, length = item["start"], item["length"]
        actual[start:start + length] = b"\0" * length
        expected[start:start + length] = b"\0" * length
raise SystemExit(0 if actual == expected else 1)
PY
  [[ $(cast call "$addr" 'POSEIDON_T3()(address)' --rpc-url "$RPC") == "$T3" ]]
  [[ $(cast call "$addr" 'POSEIDON_T4()(address)' --rpc-url "$RPC") == "$T4" ]]
}

CHAIN_ID=$(cast chain-id --rpc-url "$RPC")
[[ $CHAIN_ID == 8141 ]] || { echo "wrong chain id: $CHAIN_ID (expected 8141)" >&2; exit 1; }
HEAD=$(cast rpc --rpc-url "$RPC" eth_getBlockByNumber latest false)
[[ $(jq -r '.slotNumber // empty' <<<"$HEAD") != "" ]] || {
  echo "RPC does not expose EIP-7843 slotNumber" >&2; exit 1;
}

echo "==> Groth16 verifier (TESTBED zkey)"
VERIFIER=$(forge create --root "$BN" --rpc-url "$RPC" --private-key "$DEPLOYER_PK" "${PRICE[@]}" \
  --gas-limit 6000000 --broadcast src/Groth16Verifier.sol:Groth16Verifier | deployed)
VERIFIER_CODE=$(forge inspect --root "$BN" src/Groth16Verifier.sol:Groth16Verifier deployedBytecode)
[[ $(cast code "$VERIFIER" --rpc-url "$RPC") == "$VERIFIER_CODE" ]] || {
  echo "verifier runtime mismatch" >&2; exit 1;
}
echo "    verifier=$VERIFIER"

echo "==> immutable Poseidon libraries"
T3=$(FOUNDRY_PROFILE=libsmall forge create --root "$BN" --rpc-url "$RPC" --private-key "$DEPLOYER_PK" \
  "${PRICE[@]}" --gas-limit 12500000 --broadcast src/PoseidonT3.sol:PoseidonT3 | deployed)
T4=$(FOUNDRY_PROFILE=libsmall forge create --root "$BN" --rpc-url "$RPC" --private-key "$DEPLOYER_PK" \
  "${PRICE[@]}" --gas-limit 16000000 --broadcast src/PoseidonT4.sol:PoseidonT4 | deployed)
T3_CODE=$(FOUNDRY_PROFILE=libsmall forge inspect --root "$BN" src/PoseidonT3.sol:PoseidonT3 deployedBytecode)
T4_CODE=$(FOUNDRY_PROFILE=libsmall forge inspect --root "$BN" src/PoseidonT4.sol:PoseidonT4 deployedBytecode)
echo "    poseidonT3=$T3 poseidonT4=$T4"
verify_library_runtime "$T3" "$T3_CODE" || { echo "PoseidonT3 runtime mismatch"; exit 1; }
verify_library_runtime "$T4" "$T4_CODE" || { echo "PoseidonT4 runtime mismatch"; exit 1; }

echo "==> settlement logic"
LOGIC=$(forge create --root "$BN" --rpc-url "$RPC" --private-key "$DEPLOYER_PK" "${PRICE[@]}" \
  --gas-limit 14000000 --broadcast src/ShieldedPoolLogic.sol:ShieldedPoolLogic \
  --constructor-args "$T3" "$T4" | deployed)
LOGIC_BYTECODE=$(forge inspect --root "$BN" src/ShieldedPoolLogic.sol:ShieldedPoolLogic bytecode)
LOGIC_ARGS=$(cast abi-encode 'constructor(address,address)' "$T3" "$T4")
LOGIC_INIT="${LOGIC_BYTECODE}${LOGIC_ARGS#0x}"
EXPECTED_LOGIC=$(cast call --rpc-url "$RPC" --create "$LOGIC_INIT")
verify_logic_runtime "$LOGIC" "$EXPECTED_LOGIC" || {
  echo "logic runtime mismatch" >&2; exit 1;
}
echo "    logic=$LOGIC"

echo "==> immutable dispatcher/pool"
DISP_INIT=$(python3 dispatcher.py --initcode "$LOGIC" "$VERIFIER")
POOL=$(cast send --rpc-url "$RPC" --private-key "$DEPLOYER_PK" "${PRICE[@]}" --gas-limit 4000000 \
  --create "$DISP_INIT" --json | addr_of)
[[ $(cast code "$POOL" --rpc-url "$RPC") == $(cast call --rpc-url "$RPC" --create "$DISP_INIT") ]] || {
  echo "dispatcher runtime mismatch" >&2; exit 1;
}

SOURCE0=$(cast call "$POOL" 'sourceId(uint64)(bytes32)' 0 --rpc-url "$RPC")
DOMAIN=$(cast call "$POOL" 'domain()(bytes32)' --rpc-url "$RPC")
echo "    pool=$POOL source0=$SOURCE0 domain=$DOMAIN"

echo "==> deployment-bound proofs"
python3 ../wallet/gen_smoke.py --chain-id="$CHAIN_ID" --pool-address="$POOL" --output="$SMOKE_OUTPUT"

python3 - "$RPC" "$POOL" <<'PY'
import json, sys
with open("deploy_config.json", "w") as f:
    json.dump({"rpc": sys.argv[1], "pool": sys.argv[2]}, f, indent=1)
PY

echo "==> shield fixture note"
python3 pool_frametx.py "$RPC" deploy_config.json "$SMOKE_OUTPUT" shield "$DEPLOYER_PK"

echo "==> publish authenticated post-shield root"
PUB=$(cast send "$POOL" 'publishEpochRoot(uint64)' 0 --rpc-url "$RPC" --private-key "$DEPLOYER_PK" \
  "${PRICE[@]}" --gas-limit 500000 --json)
PUB_TX=$(jq -r '.transactionHash' <<<"$PUB")
# A shallow reorg can re-include the publication in a different consensus
# slot. Wait for two successors, then re-read the canonical receipt and block
# by hash. The spend wallet still checks the recent-root storage before signing.
while :; do
  PUB=$(cast receipt "$PUB_TX" --rpc-url "$RPC" --json)
  PUB_BLOCK=$(cast to-dec "$(jq -r '.blockNumber' <<<"$PUB")")
  HEAD_BLOCK=$(cast block-number --rpc-url "$RPC")
  (( HEAD_BLOCK >= PUB_BLOCK + 2 )) && break
  sleep 1
done
PUB_HASH=$(jq -r '.blockHash' <<<"$PUB")
BLOCK=$(cast rpc --rpc-url "$RPC" eth_getBlockByHash "$PUB_HASH" false)
ROOT_SLOT=$(jq -r '.slotNumber' <<<"$BLOCK")
[[ $ROOT_SLOT != null && $ROOT_SLOT != "" ]] || { echo "publication block has no slotNumber" >&2; exit 1; }
ROOT_SLOT_DEC=$(cast to-dec "$ROOT_SLOT")

python3 - "$RPC" "$POOL" "$VERIFIER" "$T3" "$T4" "$LOGIC" "$SOURCE0" "$DOMAIN" "$ROOT_SLOT_DEC" <<'PY'
import json, sys
keys = ["rpc", "pool", "verifier", "poseidonT3", "poseidonT4", "logic",
        "sourceIdEpoch0", "domain", "_slot_transfer"]
cfg = dict(zip(keys, sys.argv[1:]))
cfg.update({"chainId": 8141, "profile": "hegota-eip8369-testbed",
            "verifyGas": 320000, "settleGas": 2000000,
            "testbedProvingKey": True})
with open("deploy_config.json", "w") as f:
    json.dump(cfg, f, indent=1)
print("wrote deploy_config.json")
PY

echo "==> deployed testbed pool"
echo "    fixture=$SMOKE_OUTPUT"
echo "    root slot=$ROOT_SLOT_DEC (EIP-7843 slotNumber, not block timestamp)"
