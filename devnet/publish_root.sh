#!/usr/bin/env bash
# Sourced by the devnet scripts. Needs POOL, RPC, DEPLOYER_PK and the PRICE array.
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
