# Hegotá reset and merged-PR compatibility run (2026-07-30)

## Target

The reset network reported chain ID `3151908` and
`ethrex/v22.0.0-hegota-devnet-a2302ead0b7d2e076b4ab00406057d583d1f4b1f`.
The faucet was intentionally unavailable, so the run used the temporary
prefunded wallet shared for the private phase of the devnet. No private key is
stored here.

A fresh dispatcher stack was deployed. The pool is
`0xea7f8d6e14f6d5c870fa10dafe90d4c5277e9f80`, with EIP-8272 source ID
`0xd560da04606e2b79487690b22d034c6c27cf3fb01c007bfee776e55a8403772f`.
The source ID independently equals
`keccak256(pool_address_20_bytes || bytes32(0))`.

## Compatibility changes under test

The implementation was moved to the merged protocol forms before deployment:

- EIP-8141 signatures use the deployed type numbers
  `ARBITRARY = 0`, `SECP256K1 = 1`, `P256 = 2`, with the secp256k1 recovery
  identifier encoded as bare `0` or `1`. The encoder also prices the exact
  transaction data fields and EIP-8038 recent-root intrinsic cost. Its RLP and
  signing hash match ethrex's deployed golden vector.
- EIP-8250 nonce binding uses `TXPARAM(0x0E)`, the standard
  `keccak256(uint256(key_count) || key_0 || ... || key_n)` value. The pool no
  longer depends on ethrex's earlier `NONCEKEYLOAD` extension.
- EIP-8272 source IDs use the 52-byte preimage
  `address20 || salt32`, not a 64-byte ABI-padded address.

The deterministic and dispatcher-bound Groth16 fixtures were regenerated
because the source-ID change deliberately changes the proof domain. Yul
artifacts compiled, the ethrex encoder vector passed, and all 132 Forge tests
passed before the live run.

## Lifecycle

Every nullifier-consuming transaction was accepted by
`ethrex_simulateFrameTransaction` with settlement status `success` before it
was broadcast.

| Operation | EL block | Gas | Result | Dora |
| --- | ---: | ---: | --- | --- |
| Shield | 12,998 | 1,115,713 | type `0x06`, status `1` | [slot 14436](https://dora.hegota.ethrex.xyz/slot/14436) |
| Private transfer | 13,004 | 1,436,410 | pool is sender and payer, status `1` | [slot 14442](https://dora.hegota.ethrex.xyz/slot/14442) |
| Private withdrawal | 13,009 | 1,585,512 | pool is sender and payer, status `1` | [slot 14447](https://dora.hegota.ethrex.xyz/slot/14447) |

Transaction hashes:

- shield:
  `0xc6c29421e06507ca5c79a4f0a68ec70594e67c2364c5bf760d6fab8f3b777fd2`
- transfer:
  `0x8dbcdb25a6e69f123abb10eac2a25198293fb893d1e967c451af434b0207d6e1`
- withdrawal:
  `0xe2336ee8533c77c778c6473a3ab67042f288115beeb7b832415125afff7b0afa`

After the lifecycle, `nextIndex == 5`, the final tree root was
`0x10cb380f1ff473815209ee2b7311872342be1e84d37d905622983502b4ca0df5`,
the recipient had a `0.55 ETH` withdrawal credit, and self-pay created no fee
credit. Re-simulating the transfer returned
`Nonce mismatch: expected 1, got 0`, confirming admission-level replay
protection.

## Same-sender concurrency

The live tree was reconstructed from its five `LeafAppended` events. Two new
0.1 test-ETH notes were shielded, producing one shared root. Two transfers
proved membership against that same root and used disjoint EIP-8250 key sets.
The first attempt correctly failed in frame 0 because the proof-bound
0.005 ETH fee did not cover the default 1 gwei worst-case gas charge. Nothing
was broadcast or consumed. Both transactions then simulated successfully with
an explicit 0.4 gwei fee cap and 0.1 gwei tip.

The two raw transactions were submitted back-to-back from the same pool sender
at `nonce_seq = 0`. Both mined in EL block 13,069:

| Transaction | Index | Gas | Result |
| --- | ---: | ---: | --- |
| `0x0dd23e71dd5a5e9da2af2206e13e9aa35ca9d2e57a7993268756f6b6e511596b` | 0 | 1,508,507 | status `1` |
| `0x332fa4712a0607c7bebb9b0d2c28b40b37f2ba9ab4f94da4938a49b623c04635` | 1 | 1,341,398 | status `1` |

[Dora slot 14507](https://dora.hegota.ethrex.xyz/slot/14507) shows execution
block 13,069 and both transactions. Re-simulating either raw transaction now
returns `Nonce mismatch: expected 1, got 0`. The pool finished at
`nextIndex == 11`, with root
`0x035f3ede6306490b4e59a95e65e9ee7d7c21c952cd255785ae6543bc024666ec`.

## Dora limitation

Dora indexes the execution blocks and lists the type-`0x06` hashes in each
slot's transaction tab. Its direct `/tx/<hash>` pages return `404`, and its
transaction search returns `null` for these hashes. The transactions are
therefore visible through their slot pages, but the current execution indexer
does not render type-`0x06` transaction detail pages.
