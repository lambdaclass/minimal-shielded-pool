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
MANIFEST=../activation_manifest.testbed.json
python3 ../tooling/check_activation.py "$MANIFEST" --allow-testbed

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

# Publish the current tree root and echo the EIP-7843 slot its block landed in. Each
# spend proof is bound to the root that existed when it was generated, so a spend that
# changes the tree invalidates the root the next one needs: the transfer and the withdraw
# are bound to different roots and each needs its own publication.
publish_root() {
  local pub pub_tx pub_block head_block pub_hash block slot
  pub=$(cast send "$POOL" 'publishEpochRoot(uint64)' 0 --rpc-url "$RPC" \
    --private-key "$DEPLOYER_PK" "${PRICE[@]}" --gas-limit 500000 --json)
  pub_tx=$(jq -r '.transactionHash' <<<"$pub")
  # A shallow reorg can re-include the publication in a different consensus slot. Wait
  # for two successors, then re-read the canonical receipt and block by hash. The spend
  # wallet still checks the recent-root storage before signing.
  while :; do
    pub=$(cast receipt "$pub_tx" --rpc-url "$RPC" --json)
    pub_block=$(cast to-dec "$(jq -r '.blockNumber' <<<"$pub")")
    head_block=$(cast block-number --rpc-url "$RPC")
    (( head_block >= pub_block + 2 )) && break
    sleep 1
  done
  pub_hash=$(jq -r '.blockHash' <<<"$pub")
  block=$(cast rpc --rpc-url "$RPC" eth_getBlockByHash "$pub_hash" false)
  slot=$(jq -r '.slotNumber' <<<"$block")
  [[ $slot != null && $slot != "" ]] || { echo "publication block has no slotNumber" >&2; return 1; }
  cast to-dec "$slot"
}

echo "==> publish authenticated post-shield root"
ROOT_SLOT_DEC=$(publish_root) || exit 1

MANIFEST_PATH=$MANIFEST python3 - "$RPC" "$POOL" "$VERIFIER" "$T3" "$T4" "$LOGIC" "$SOURCE0" "$DOMAIN" "$ROOT_SLOT_DEC" <<'PY'
import json, os, sys
keys = ["rpc", "pool", "verifier", "poseidonT3", "poseidonT4", "logic",
        "sourceIdEpoch0", "domain", "_slot_transfer"]
cfg = dict(zip(keys, sys.argv[1:]))
# The gas figures come from the manifest this deployment was actually gated on, not
# from literals. A deployment of the updated dispatcher that wrote the frozen profile's
# 2,000,000 single budget would hand the spend wallet budgets belonging to a contract it
# is not talking to, and the resulting settlement failures look like proof errors.
manifest = json.load(open(os.environ["MANIFEST_PATH"]))["profile"]
cfg.update({"chainId": manifest["chain_id"], "profile": manifest["wire_profile"],
            "verifyGas": manifest["verify_frame_gas"],
            "settleGas": manifest["settle_frame_gas"],
            "testbedProvingKey": True})
if "settle_frame_state_gas" in manifest:
    cfg["settleStateGas"] = manifest["settle_frame_state_gas"]
    cfg["verifyStateGas"] = manifest["verify_frame_state_gas"]
with open("deploy_config.json", "w") as f:
    json.dump(cfg, f, indent=1)
print("wrote deploy_config.json")
PY

echo "==> deployed testbed pool"
echo "    fixture=$SMOKE_OUTPUT"
echo "    root slot=$ROOT_SLOT_DEC (EIP-7843 slotNumber, not block timestamp)"

# A deployment that only shields proves the pool can take money, not that it can pay it
# out. The spends are the half that exercises the proof, the nullifier, the recent-root
# reference and the settlement frame's gas — the parts a wire-profile change actually
# threatens — so run them here rather than leaving "fully deployed" to mean "half tested".
# SPEND=0 skips them for a deployment that is only publishing a pool.
if [[ ${SPEND:-1} == 1 ]]; then
  echo "==> transfer (shielded spend, note -> note)"
  python3 pool_frametx.py "$RPC" deploy_config.json "$SMOKE_OUTPUT" transfer "$DEPLOYER_PK"

  # The transfer inserted two commitments, so the root the withdraw proof was generated
  # against is the post-transfer one, not the post-shield one already published. Publish
  # again and record its slot under the key the withdraw reads. Without this a withdraw
  # after a fresh deployment dies on a missing `_slot_withdraw`, which is why the flow
  # had only ever been run against a config edited by hand.
  echo "==> publish authenticated post-transfer root"
  WITHDRAW_SLOT=$(publish_root) || exit 1
  python3 - "$WITHDRAW_SLOT" <<'PY'
import json, sys
cfg = json.load(open("deploy_config.json"))
cfg["_slot_withdraw"] = sys.argv[1]
json.dump(cfg, open("deploy_config.json", "w"), indent=1)
print(f"    withdraw root slot={sys.argv[1]}")
PY

  echo "==> withdraw (shielded spend, note -> credit)"
  python3 pool_frametx.py "$RPC" deploy_config.json "$SMOKE_OUTPUT" withdraw "$DEPLOYER_PK"

  # A withdraw books a credit; it does not push funds. Until the credit is claimed the
  # recipient's balance is unchanged and the pool still holds the money, so a run that
  # stops at the withdraw proves the proof verified and nothing about the payout.
  # 900k rather than a round 200k: the claim measured 216,740 gas here, and at 200,000 it
  # runs out mid-payout and reverts having consumed the lot.
  #
  # The payout is judged as a balance delta, not as "nonzero afterwards": the fixture's
  # recipient is a fixed address, so on a chain that has seen one successful run it is
  # already funded and a reverted claim would otherwise pass.
  RECIPIENT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["recipient"])' "$SMOKE_OUTPUT")
  PUBLIC_AMOUNT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["withdraw"]["public_amount"])' "$SMOKE_OUTPUT")
  BEFORE=$(cast balance "$RECIPIENT" --rpc-url "$RPC")
  echo "==> claim (credit -> recipient $RECIPIENT, expecting +$PUBLIC_AMOUNT wei)"
  claim=$(cast send "$POOL" 'claimWithdrawal(address)' "$RECIPIENT" --rpc-url "$RPC" \
    --private-key "$DEPLOYER_PK" "${PRICE[@]}" --gas-limit 900000 --json)
  [[ $(jq -r '.status' <<<"$claim") == "0x1" ]] || {
    echo "claim reverted: $(jq -r '.transactionHash' <<<"$claim")" >&2; exit 1; }
  AFTER=$(cast balance "$RECIPIENT" --rpc-url "$RPC")
  # Balances outgrow bash's 64-bit arithmetic after a few ETH, so subtract in python.
  PAID=$(python3 -c 'import sys; print(int(sys.argv[1]) - int(sys.argv[2]))' "$AFTER" "$BEFORE")
  [[ $PAID == "$PUBLIC_AMOUNT" ]] || {
    echo "claim paid $PAID wei to the recipient, expected $PUBLIC_AMOUNT" >&2; exit 1; }
  echo "    recipient +$PAID wei ($BEFORE -> $AFTER)"
  echo "==> spends settled"
fi
