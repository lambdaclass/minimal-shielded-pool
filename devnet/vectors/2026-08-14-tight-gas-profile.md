---
type: research
tags: [privacy]
status: in-progress
updated: 2026-08-14
---

# Minimal pool tight gas profile

## TL;DR

The immutable spend profile is reduced from 400,000 to 320,000 VERIFY gas and from 10,000,000 to 2,000,000 SENDER gas. The largest live VERIFY measurement is 294,401. The heaviest reachable local settlement shape uses 832,626 gas with real Poseidon, and a deliberately double-counted EIP-8037/8038 storage adjustment bounds it at 1,721,526 gas on the active ethrex fork. This is activation evidence for that fork only, not a promise across future gas repricing.

## Limits

| Frame | Old limit | New limit | Evidence | Margin |
| --- | ---: | ---: | ---: | ---: |
| VERIFY execution | 400,000 | 320,000 | 294,401 live maximum | 25,599 |
| SENDER settlement | 10,000,000 | 2,000,000 | 1,721,526 conservative bound | 278,474 |

The one secp256k1 signature adds 2,800 gas to the validation prefix, so the declared EIP-8369 verification budget is 322,800 gas.

## Settlement bound

The tested maximum path starts at `nextIndex = 2^20 - 1` with every filled subtree populated. One call finalizes the old epoch, clears it, appends two ordinary commitments using the real Poseidon runtimes, computes the new root, and creates a first withdrawal credit. Forge measures 832,626 gas through the same proxy and self-call nesting used by settlement.

That path executes at most 33 storage writes and creates at most five new storage slots. The executable checker keeps the full 832,626-gas measurement, even though it already includes legacy storage charges, then adds a full cold EIP-8038 write charge for all 33 writes and the EIP-8037 state-growth charge for all five new slots:

`832,626 + 33 × 12,100 + 5 × (64 × 1,530) = 1,721,526`

This intentionally double-counts storage gas. The 2,000,000 cap is 278,474 gas above that conservative result. Root publication and recipient calls cannot enter this path. Any future gas repricing requires a new immutable profile before activation.

## Live ethrex result

A fresh immutable pool was deployed at `0xb88fe33e0ff980f8eb1e54606f6ef25242b1c00e` on chain ID 8141. The exact tightened profile completed the full lifecycle:

| Operation | Transaction | VERIFY gas | SENDER gas | Result |
| --- | --- | ---: | ---: | --- |
| Shield | `0x474f296fb035a1c08061f47622f2dfff7c0e0f367d343bb6b3641c9da624ef92` | 0 | 1,078,890 | Pass |
| Private transfer | `0x90cf2ad1f42189ead3c493e5c9d4077967e66fa05feba4b80233b350f80ccdcf` | 294,374 | 1,096,545 | Pass |
| Private withdrawal | `0xfd7dbc577eaf1f97b97f1a9fd4ea7e8e5140a207bceb0929b182d59c7ad66bf0` | 294,374 | 126,015 | Pass |
| Pull claim | `0xe56a0037da4f86c5ba3108b1869a2df74c3b1fc048b1251f23b75b23955a0ec4` | n/a | n/a | Pass |

The withdrawal created and then cleared exactly 0.55 ETH of credit. The final tree index is three, the pool retains `0.448115027986805196` ETH, and the recipient received the expected 0.55 ETH. Exact-byte replay of both spends failed at validation with `Nonce mismatch: expected 1, got 0`.

One root-publication block was shallow-reorged and re-included one consensus slot later. The wallet's EIP-8272 storage self-check refused to sign against the stale slot, no nullifier was consumed, and retrying with the canonical receipt slot succeeded. The deployment helper now waits for two successors and re-reads the publication by canonical block hash; the wallet check remains the final fail-closed guard.

## Reproduce

```sh
cd prototypes/minimal-shielded-pool/contracts
forge test -vvvv --match-test test_two_million_gas_covers_heaviest_reachable_settlement_shape

cd ../tooling
python3 check_gas_profile.py
```

## See also

- [[prototypes/minimal-shielded-pool/index]]
- [[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-14-hardened-pool]]
