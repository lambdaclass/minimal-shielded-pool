# Minimal shielded pool

An immutable, native-ETH shielded pool built for the ethrex Hegotá testnet's
EIP-8141 Frame Transactions, EIP-8250 keyed nonces, and EIP-8272 recent roots.
It has no ERC-20 path, admin, governance, external paymaster, or MATCHA-specific
mempool mechanism.

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

## Compatibility target

The active encoder and immutable dispatcher target the wire format deployed by
ethrex v23 on the Hegotá testnet, chain ID 8141. That network uses an 11-field
envelope, one gas limit per frame, and the gas schedule recorded in the testbed
activation manifest.

The Ethereum EIPs remain drafts. The
[current EIP-8141 draft](https://eips.ethereum.org/EIPS/eip-8141) has since
changed to a nested `fees` field and separate execution and state gas limits.
The existing deployment and its signed transactions are therefore not
compatible with that newer wire format. Supporting it requires a separately
versioned encoder, dispatcher, gas profile, and deployment. The tested Hegotá
path stays frozen until ethrex activates such a profile.

## Test

```sh
npm ci --prefix tooling
python3 -m pip install --requirement requirements.txt

python3 devnet/frametx.py
python3 devnet/test_pool_envelope_binding.py
python3 tooling/check_gas_profile.py
python3 tooling/check_activation.py activation_manifest.testbed.json --allow-testbed
python3 wallet/wallet.py
python3 reference/poseidon_bn254.py

forge fmt --root contracts --check
forge lint --root contracts --deny warnings
forge test --root contracts --force -vv
```

CI also recompiles the circuit in a temporary directory and compares the R1CS
and WASM byte for byte with the committed artifacts. Run `tooling/setup.sh`
only when intentionally replacing the disposable proving setup. It randomizes
the phase-2 contribution and changes the proving key, verification key, and
verifier, so the activation manifest and proof fixtures must then be rebuilt.
`tooling/check_activation.py` checks every pinned artifact and fails closed on
the testbed manifest unless `--allow-testbed` is explicit.

The direct proving-tool versions are also pinned to the committed artifact
provenance. In particular, circom2 0.2.23 does not reproduce the committed
0.2.8 R1CS or WASM byte for byte, so a compiler upgrade belongs to a new
reviewed artifact set rather than routine dependency maintenance.

## Compatibility

| Dependency | Status |
|---|---|
| Ethrex v23 Hegotá FrameTx ABI | Implemented and tested live |
| Current EIP-8141 wire format | Not implemented: it differs from the deployed ethrex testnet format |
| EIP-8141 published 100k public-mempool budget | Not compatible: the Hegotá profile declares 320k plus 2.8k signature gas |
| EIP-8250 keyed nonces | Implemented in the Hegotá profile: exactly two sorted proof nullifiers, sequence zero |
| EIP-8272 recent roots | Implemented in the Hegotá profile: per-epoch source, exact source/slot/root binding, permissionless publication |
| EIP-7843 slot number | Implemented: wallet requires the RPC `slotNumber` field |
| EIP-8369 | The open draft does not set a final per-transaction budget; the Hegotá testnet currently admits this 322.8k profile |
| Current ethrex privacy testnet | ethrex v23 was live on chain 8141 on 2026-08-14; the 320k/2M profile passed shield, transfer, withdrawal, claim, and replay rejection |

The current testnet evidence is in
[`devnet/vectors/2026-08-14-tight-gas-profile.md`](devnet/vectors/2026-08-14-tight-gas-profile.md).
The published EIP-8141 100k policy remains a portability blocker. The pool must
use a network profile that explicitly admits its 322.8k validation budget. The
current Hegotá testnet does. [EIP-8369](https://github.com/ethereum/EIPs/pull/12110)
is still an open Informational proposal; its `2^20` per-IL value is a benchmark
candidate, not a finalized per-transaction consensus limit.

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
