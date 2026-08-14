# Proof in the VERIFY frame, publics-only settlement: live validation

Date: 2026-07-17
Chain: Hegotá ethrex devnet, chain ID `3151908`
Endpoint: `https://rpc1.hegota.ethrex.xyz`

Validates the settlement/proof split. The Groth16 proof no longer rides in the
settlement tuple: the frame-0 VERIFY frame carries the 256-byte proof as its
calldata (validation-surface data), and the SENDER frame settles a
publics-only 288-byte `Spend`. This makes the proof a detachable unit for the
EIP-8288 aggregation path (or the EIP-8141 signatures-list carrier), with no
statement or circuit change: same nine publics, same verification key, same
proofs. The `Spend` tuple drops `pA/pB/pC`; `transfer`/`withdraw` selectors
become `0x4ebb583c` / `0x5f0c5052`, and `verifyProofOnly(Spend,uint256[2],
uint256[2][2],uint256[2])` is `0x76314392`.

## Deployment (split architecture, fresh)

- Pool / dispatcher: `0xdd93876aed0cf62afd8568ed7522dbab72452164`
- Logic (publics-only settlement): `0x6a97CC25597bE74EE25670c9A76851d5d1FbfAD6`
- Envelope probe (with proof passthrough): `0xe6ecce2c95b9bf33f3e18df1c5a06072f0c7bed0`
- Groth16 verifier / Poseidon T3 / T4: reused immutables from prior runs
- Source ID: `0x8e27f637697e72617087ed2a88a5d82005f2140bdb9ddacfe44519c95cae396f`

## Lifecycle (self-paying, zero-balance outer signer)

| Action | Transaction | Block | Gas used |
|---|---|---:|---:|
| Shield 0.1 ETH | `0xc71683257c64290e5ebd182052a32d03523835061ffedf69e08ae3c66d6a1010` | 225024 | 1,083,133 |
| Private transfer | `0x6080619be09e6bca526e222031b9d059a0b26ea9b6cd8b3f27f54e6ea9ecabe7` | 225026 | 1,384,692 |
| Private withdrawal | `0xb755ba0edb5df8a90d52803248159b6366bc9d7370e7a18ef2eb01fcf9d2b3a2` | 225030 | 1,524,790 |

Both spends decoded (and Dora's frame view confirms) to exactly two frames:

```text
0. VERIFY(pool, execution+payment)  data = 256 B (the proof)   gas 285,750
1. SENDER(pool)                     data = 292 B (publics tuple) gas 1,064,338
```

Gas is within ~800 of the pre-split run (the proof bytes moved frames, they
were never duplicated). Post-state exact: 0.02 ETH withdrawal credit,
`feeCredit(pool) == 0`. Re-publishing either exact mined transaction was
rejected at admission (`Nonce mismatch: expected 1, got 0`).

## Adversarial negatives (fresh empty pool2 `0x060e191c…`)

- Flipped proof bit (now carried only in frame 0): rejected in the validation
  prefix (`validation prefix frame reverted`), proving frame-0 inline
  verification reads the relocated proof.
- Settlement frame down-gassed to 5,000,000: same prefix rejection.
- The unmodified spend then mined (`0x2632a6c096ea4d312d7195aed86314999d0c71f92f8e903cc083b8b22198ba99`, block 225052).

## Artifacts

- `deploy_config.json`, `smoke_fixture.json`: the lifecycle pool.
- `transfer.raw`, `withdraw.raw`: exact mined type-`0x06` transactions.
- `SHA256SUMS`: integrity hashes.
