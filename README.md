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

Each spend has exactly three frames:

1. `VERIFY(0x…8272, tuple)`, EIP-8272's canonical recent-root verifier. The
   protocol runs `RECENT_ROOT_CODE` over the 72-byte tuple before any pool code
   runs, so the root is already proven when frame 1 begins.
2. `VERIFY(pool, proof)`, which verifies the proof and exact envelope, then
   approves execution and payment.
3. `SENDER(pool, settle(Spend))`, which performs bounded internal settlement.

The proof chooses a fresh secp256k1 authorizer. Its sole EIP-8141 empty-message
signature covers the canonical hash of the complete transaction, including
the proof bytes, nonce keys, the recent-root frame, frames, gas limits, fee
fields, and settlement calldata. Raw signature bytes alone are elided by EIP-8141.

The pool is both sender and payer. Its two proof nullifiers are the complete
EIP-8250 key set at sequence zero. The transaction leads with EIP-8272's
canonical recent-root verifier frame, whose 72-byte `(source_id, slot, root)`
tuple the predeploy checks before any pool code runs; the dispatcher binds that
exact tuple through `FRAMEDATALOAD` and requires the frame's shape and success
through `FRAMEPARAM`. Slots come directly from EIP-7843 `slotNumber`;
timestamp reconstruction is rejected.

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

The active encoder and immutable dispatcher follow current EIP-8141, EIP-8250 at
`f3079a09e8` (merged PRs 12279 and 12316), and EIP-8272 at `824cbc0b0e` (PRs 12281
and 12302). They use EIP-8141's nested `fees` field and separate execution and
state gas limits for each frame; the envelope has eight fields. PR 12316 is what
put the nested `fees` list back into EIP-8250's own payload block, which had
flattened the three fee parameters into the outer list; the encoder here always
nested them, so the correction confirms the shape rather than changing it. Every private
spend gives its proof frame `195,840` state gas to create its two nullifier
keys, and leads with a `30,000`-gas recent-root verifier frame that counts
toward the public mempool's verify budget. The gas schedule is recorded in the
testbed activation manifest, wire profile `eip8272-canonical-frame`.

This profile targets the chain 8141 testnet's next re-genesis, which moves the
node to those revisions; the chain launched on September 3 runs the older
EIP-8250 gas rule and the envelope-field form of EIP-8272 and cannot decode
these transactions. The dialect deployed on the pre-relaunch chain-8141 testnet
(11-field envelope, one gas limit per frame) is archived byte-exact under
`devnet/vectors/2026-09-01-hegota-final-profile/`, the auditable record of that
deployment.

The Ethereum EIPs remain drafts, so each supported combination is a separate
versioned profile. The archived profiles and the active profile are not wire
compatible.

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
| Ethrex v23 Hegotá FrameTx ABI | Implemented, and the whole lifecycle mined on a devnet at these pins |
| Current EIP-8141 wire format | Implemented by the active encoder and tested on a private three-node devnet |
| EIP-8141 published 100k public mempool budget | Not compatible: the two validation frames and the signature need 352.8k execution gas |
| EIP-8250 keyed nonces | The pool follows PR 12279: two fresh keys cost `195,840` state gas in the proof frame |
| EIP-8272 recent roots | The pool follows `824cbc0b0e`: the root travels in the canonical verifier frame that leads the transaction |
| EIP-7843 slot number | Implemented: wallet requires the RPC `slotNumber` field |
| EIP-8369 | The open draft does not set a final per-transaction budget; the devnet used for this profile admits the 352.8k budget |
| Current ethrex privacy testnet | The chain relaunched on 2026-09-14 on this profile; the whole lifecycle mined on it |
| MATCHA (ethrex mempool capacity) | Compatible: spends clear the structural EIP-8250 test, so the pool can hold several pending spends once it has earned width; the wallet rides out the refusal a fresh pool gets, see below |

Earlier testnet evidence is in
[`devnet/vectors/2026-08-14-tight-gas-profile.md`](devnet/vectors/2026-08-14-tight-gas-profile.md).
The published EIP-8141 100k policy remains a portability blocker. The pool must
use a network profile that explicitly admits its 352.8k validation budget, which
the live chain does not yet. [EIP-8369](https://github.com/ethereum/EIPs/pull/12110)
is still an open Informational proposal; its `2^20` per-IL value is a benchmark
candidate, not a finalized per-transaction consensus limit.

### Several spends in flight

ethrex admits a sender's first pending frame transaction for free and charges every
additional one against width the sender earned from the gas its own frame transactions
used in finalized blocks (MATCHA, `docs/matcha.md` in ethrex). Two things follow for a
pool that relays spends for many users.

The pool qualifies. A spend's keyed nonces are its own two nullifiers, its sender is the
pool contract, and its validation prefix reads no pool storage (the root comes from the
EIP-8272 verifier frame, the proof from the transaction), so ethrex's structural test
allows a second pending spend alongside the first. Width is the only remaining gate.

A fresh pool has none. Until its first spend finalizes it holds one pending spend, and a
second is refused with `sender has 0 MATCHA width, needs N for an additional frame
transaction`. The wallet does not have to find that out by being refused: the simulation it
runs before every send reports `matchaCharge` and `matchaAdmissible`, and
`ethrex_matchaWidth(pool)` reads the ledger, so `devnet/pool_frametx.py --wait-width S`
holds a spend that does not fit until the pending one mines or finality credits width. From
then on each finalized spend of about 1.6M gas buys about three more pending spends at the
3/2 charge, up to the node's cap. `--no-wait` returns at submission so a second spend can
be sent while the first is pending, and `devnet/run_concurrent_spends.sh` runs both regimes
against a deployed pool, printing the ledger before each pair. Older nodes without the
endpoints still work: the refusal on send is parsed and retried.

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
