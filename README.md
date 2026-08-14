# Minimal shielded pool

An immutable, native-ETH shielded pool built for EIP-8141 Frame Transactions,
EIP-8250 keyed nonces, and EIP-8272 recent roots. It has no ERC-20 path, admin,
governance, external paymaster, or MATCHA-specific mempool mechanism.

This repository is research software. The committed Groth16 proving key is a
single-party testbed key and must never protect real value.

## Design

A note is `Poseidon(TAG_LEAF, Poseidon(spend_key, rho), value)`. A spend proves
a 2-input/2-output join-split with ten public signals:

`[nf1, nf2, outCm1, outCm2, root, domain, publicAmount, fee, recipient, authorizer]`

The circuit enforces membership for positive inputs, value conservation,
128-bit amounts, at least one positive input, distinct nullifiers, distinct
outputs, two position-specific zero-value sinks, a nonzero `uint160`
authorizer, and the transfer/withdrawal recipient shape.

Each spend has exactly two frames:

1. `VERIFY(pool, proof)`, which verifies the proof and exact envelope, then
   approves execution and payment.
2. `SENDER(pool, settle(Spend))`, which performs bounded internal settlement.

The proof chooses a fresh secp256k1 authorizer. Its sole EIP-8141 empty-message
signature covers the canonical hash of the complete transaction, including
the proof bytes, nonce keys, root reference, frames, gas limits, fee fields,
and settlement calldata. Raw signature bytes alone are elided by EIP-8141.

The pool is both sender and payer. Its two proof nullifiers are the complete
EIP-8250 key set at sequence zero. The dispatcher binds the exact EIP-8272
`(source_id, slot, root)` tuple. Slots come directly from EIP-7843
`slotNumber`; timestamp reconstruction is rejected.

Settlement never publishes a root or calls a recipient. It rolls to a fresh
Merkle epoch before inserting outputs when capacity is insufficient. The two
zero sinks consume no capacity, so an exit remains possible at a full tree.
Withdrawals are pull credits. Root publication is a separate permissionless
call that reads only the active or finalized root stored by the pool.

## Active implementation

```
circuits/spend.circom
contracts/src/Groth16Verifier.sol
contracts/src/ShieldedPoolLogic.sol
contracts/src/PoseidonT3.sol
contracts/src/PoseidonT4.sol
devnet/ShieldedPoolDispatcher.yul
devnet/dispatcher.py
devnet/pool_frametx.py
wallet/wallet.py
wallet/gen_smoke.py
```

Unsafe historical standalone, sponsored, probe, and monolithic pool variants
were removed. Git history retains them for research, but they are not supported
deployment paths.

## Test

```
cd tooling
npm ci
./setup.sh

cd ../wallet
python3 gen_smoke.py

cd ../contracts
forge test --summary

cd ../devnet
python3 test_pool_envelope_binding.py
```

`setup.sh` regenerates a disposable trusted setup, verifier, and proof fixture.
`tooling/check_activation.py` checks every pinned artifact and fails closed on
the committed testbed manifest unless `--allow-testbed` is explicit.

## Compatibility

| Dependency | Status |
|---|---|
| EIP-8141 transaction/signature/frame ABI | Implemented |
| EIP-8141 published 100k public-mempool budget | Not compatible: the proof profile declares 320k plus 2.8k signature gas |
| EIP-8250 keyed nonces | Implemented: exactly two sorted proof nullifiers, sequence zero |
| EIP-8272 recent roots | Implemented: per-epoch source, exact source/slot/root binding, permissionless publication |
| EIP-7843 slot number | Implemented: wallet requires the RPC `slotNumber` field |
| EIP-8369 Profile 2 | Fits the 2^20 verification budget used by the current ethrex testnet |
| Current ethrex privacy testnet | The tightened 320k/2M profile passed shield, transfer, withdrawal, claim, and replay rejection on 2026-08-14 |

The current testnet evidence is in
[`devnet/vectors/2026-08-14-tight-gas-profile.md`](devnet/vectors/2026-08-14-tight-gas-profile.md).
The published EIP-8141 100k policy remains a real portability blocker. The
pool must be submitted through a network profile that explicitly admits its
322.8k validation budget, such as the tested Hegotá configuration or an
EIP-8369 heavy lane.

## Production gates

- Replace the single-party zkey with a documented multi-party phase-2
  ceremony and independent verification.
- Pin and publish the final circuit, zkey, verifier, dispatcher, logic, and
  Poseidon runtime hashes.
- Re-run the full signature-mutation, capacity, reorg, gas-boundary, and
  cross-client vectors on the exact activation fork.
- Re-run the fixed 2M SENDER-cap proof on every supported gas schedule.
  Deactivate the profile before an unsupported repricing fork.
- Obtain an independent contract and circuit audit.

See [`SECURITY.md`](SECURITY.md) for the precise trust and failure boundaries.
