#!/usr/bin/env bash
# Two shielded spends from one pool, submitted back to back, against a MATCHA node.
#
# EIP-8250 lets the pool hold several pending spends at once: each spend's keyed nonces
# are its own nullifiers and its validation prefix reads no pool storage, so ethrex's
# structural test clears it. MATCHA (ethrex docs/matcha.md) gates that on width the pool
# earns from the gas its own spends used in finalized blocks: a fresh pool holds one
# pending spend and is refused a second until its first spend finalizes. Each finalized
# spend of about 1.6M gas buys about three more at the 3/2 charge. Both regimes, on a
# deployed pool:
#
#   phase "fresh"   two notes shielded, one root published, transfer A submitted and, with
#                   no wait, transfer C. On a pool with no earned width C is refused with
#                   the node's figures, the wallet retries, and C is admitted once A mines.
#   phase "earned"  (EARNED=1) once the fresh spends are finalized, the same again with two
#                   new notes. C is admitted while A is still pending and the two usually
#                   share a block.
#
# Required: RPC_URL, DEPLOYER_PK, and a deploy_config.json from run_live_dispatcher.sh.
# Deploy with SPEND=0 to start from a pool that has never spent, so the fresh phase shows
# the refusal rather than inheriting width from the lifecycle run.
set -euo pipefail
cd "$(dirname "$0")"

RPC=${RPC_URL:?set RPC_URL}
: "${DEPLOYER_PK:?set DEPLOYER_PK}"
POOL=$(python3 -c 'import json; print(json.load(open("deploy_config.json"))["pool"])')
CHAIN_ID=$(cast chain-id --rpc-url "$RPC")
PRICE=(--gas-price 3000000000 --priority-gas-price 1000000000)
. ./publish_root.sh

# hash -> block number; a reverted spend ends the run, its notes are burned.
wait_mined() {
  local rcpt=""
  for _ in $(seq 1 60); do
    rcpt=$(cast receipt "$1" --rpc-url "$RPC" --json 2>/dev/null) && [[ -n $rcpt ]] && break
    sleep 2
  done
  [[ -n $rcpt ]] || { echo "spend $1 not mined within 120s" >&2; exit 1; }
  [[ $(jq -r '.status' <<<"$rcpt") == "0x1" ]] || { echo "spend $1 reverted" >&2; exit 1; }
  cast to-dec "$(jq -r '.blockNumber' <<<"$rcpt")"
}

# block -> returns once the finalized tag is at or past it
wait_finalized_past() {
  local fin
  while :; do
    fin=$(cast rpc --rpc-url "$RPC" eth_getBlockByNumber finalized false | jq -r '.number')
    [[ $fin != null && $fin != "" ]] && (( $(cast to-dec "$fin") >= $1 )) && break
    sleep 5
  done
}

submitted_hash() { grep -oE 'submitted: 0x[0-9a-f]{64}' <<<"$1" | awk '{print $2}'; }

LAST_BLOCK=0
phase() {
  local label=$1
  local fixture=$PWD/../wallet/artifacts/nonce_race.$label.json
  echo "==> [$label] two fresh notes proven against the live tree"
  ( cd ../wallet && python3 gen_nonce_race.py --random --chain-id="$CHAIN_ID" \
      --pool-address="$POOL" --rpc="$RPC" --pool="$POOL" --output="$fixture" )
  echo "==> [$label] shield A and C"
  python3 pool_frametx.py "$RPC" deploy_config.json "$fixture" shield "$DEPLOYER_PK" --note 0
  python3 pool_frametx.py "$RPC" deploy_config.json "$fixture" shield "$DEPLOYER_PK" --note 1
  echo "==> [$label] publish the shared root"
  local slot; slot=$(publish_root)
  echo "    root slot=$slot"

  echo "==> [$label] transfer A, submitted without waiting"
  local out_a; out_a=$(python3 pool_frametx.py "$RPC" deploy_config.json "$fixture" transfer \
      "$DEPLOYER_PK" --spend-key transfer --root-slot "$slot" --no-wait)
  sed 's/^/    /' <<<"$out_a"
  echo "==> [$label] transfer C, back to back, riding out a MATCHA refusal for up to 300s"
  local out_c; out_c=$(python3 pool_frametx.py "$RPC" deploy_config.json "$fixture" transfer \
      "$DEPLOYER_PK" --spend-key transfer_c --root-slot "$slot" --no-wait --wait-width 300)
  sed 's/^/    /' <<<"$out_c"

  local block_a block_c
  block_a=$(wait_mined "$(submitted_hash "$out_a")")
  block_c=$(wait_mined "$(submitted_hash "$out_c")")
  if grep -q "MATCHA: refused" <<<"$out_c"; then
    echo "    [$label] C refused while A was pending, admitted after A mined: A block $block_a, C block $block_c"
  else
    echo "    [$label] C admitted at first try: A block $block_a, C block $block_c"
  fi
  LAST_BLOCK=$(( block_a > block_c ? block_a : block_c ))
}

phase fresh
if [[ ${EARNED:-0} == 1 ]]; then
  echo "==> waiting for the finalized tag to pass block $LAST_BLOCK, when the fresh spends earn width"
  wait_finalized_past "$LAST_BLOCK"
  phase earned
fi
echo "==> concurrent spends settled"
