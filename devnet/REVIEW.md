# Hegotá devnet: review + shielded-pool integration

> Historical engineering log. Architectures and commands below predate the
> hardened 2026-08-14 pool and are not current deployment guidance. Use
> `../README.md` and `vectors/2026-08-14-tight-gas-profile.md` for the active shape.

A review of lambdaclass/ethrex's `hegota-devnet` (EIP-8141 frame transactions +
EIP-8250 keyed nonces + EIP-8272 recent roots, plus EIP-7906 POST_TX) and the
plan and tooling to run the BN254 shielded pool on it as a frame-native
application. Reviewed against the live network and the branch source on
2026-07-03.

## What the devnet is, verified live

- Chain id `3151908` (`0x301824`), 6s slots, three public RPCs
  (`rpc{1,2,3}.hegota.ethrex.xyz`), Dora explorer, an IP-rate-limited faucet.
  All three RPCs answer and the chain advances (observed block ~18k).
- Frame transactions are type `0x06` with the 11-field envelope
  `[chain_id, nonce_keys, nonce_seq, sender, frames, signatures,
  max_priority_fee, max_fee, max_blob_fee, blob_hashes, recent_root_references]`;
  `frame = [mode, flags, target, gas_limit, value, data]`. The encoder here,
  `frametx.py`, is vendored from the ethrex repo (see NOTICE); its
  golden-vector self-check (`python3 frametx.py`) matches the repo vector
  exactly (RLP and sig_hash).
- Predeploys confirmed by `eth_getCode`: NONCE_MANAGER at `0x…8250` carries the
  stub `0x60006000fd` (PUSH1 0; PUSH1 0; REVERT, matching the test harness),
  and RECENT_ROOT_ADDRESS at `0x…8272` has empty code (its 64-byte
  `salt‖root` write is handled natively).

## The frame execution model (from crates/vm/levm)

Four frame modes: DEFAULT (0, caller is ENTRY_POINT `0x…aa`), VERIFY (1, static
validation that grants approval), SENDER (2, executes as `tx.sender`, the only
mode that may move `value`), POST_TX (3, trailing read-only assertion). Three
new opcodes:

- `APPROVE` (0xAA): RETURN-like, sets tx-scoped approval. Pops `(offset, len,
  scope)`. The running code's address must equal the current frame's target.
  Scope `0x0` (execution) and `0x2` (both) require `frame_target == sender`;
  scope `0x1` (payment) does **not** — it only needs execution already
  approved. So a paymaster contract that is a frame's target can approve
  payment for a sender that is not itself. This is the onchain hook the
  trustless-paymaster fee design needs.
- `TXPARAMLOAD/SIZE/COPY` (0xB0/0xB1/0xB2): read transaction parameters by
  selector. Implemented selectors: tx_type(0x00), nonce(0x01), sender(0x02),
  fees(0x03–0x06), blob count(0x07), **sig_hash(0x08)**, frame count(0x09),
  current_frame_index(0x10), and per-frame target/data/gas/mode/status
  (0x11–0x15).

## Two gaps that block a fully-faithful binding

The pool's design (see `../README.md`) needs its onchain VERIFY
logic to bind each proof to the transaction envelope. On this devnet, two of
those bindings cannot be done in-EVM today:

1. **No TXPARAM selector for `nonce_keys`.** The spent-set binding — "the
   consumed nonce-key set is exactly the proven nullifiers" — cannot be checked
   inside a VERIFY frame, because no selector exposes the envelope's
   `nonce_keys`. So even if a spend put `[nf1, nf2]` in `nonce_keys` (making the
   protocol's NONCE_MANAGER the real spent set), the pool could not verify that
   those keys equal the nullifiers its proof commits to. The binding would be
   trusted, not proven. Adding a `nonce_keys(index)` + `nonce_key_count`
   selector pair would close this and is the single highest-value change for
   privacy-protocol integration.
2. **No exposure of `recent_root_references`, and slot is not delivered to the
   EL.** The root-source binding cannot be checked in-EVM (no opcode reads the
   refs), and the guide notes the consensus client does not yet deliver the
   slot number, so refs "cannot fully validate end-to-end." A pool must
   therefore keep reading its own root history rather than the protocol's
   EIP-8272 entries.

A third constraint is policy, not capability: the public mempool admits only
`nonce_keys == [0]`; non-zero-key transactions are "submittable directly to
block builders." A privacy spend that used the nullifiers as keyed nonces
cannot go through the public RPC and needs a builder endpoint.

None of these is a soundness bug in the devnet; they are the expected rough
edges of an early multi-EIP testnet, and they line up exactly with the
"bindings are trusted contract code, not proven in-circuit" caveat the pool
already states. They are the concrete work items to make the envelope bindings
enforceable rather than trusted.

## Live-run findings (2026-07-03), with onchain evidence

Attempting the real run surfaced five concrete devnet facts, each demonstrated
against the live network. Finding 4 corrects a wrong conclusion reached
earlier the same day: deployment was never blocked, the deployer key was just
underfunded, and the failure surfaced in a misleading way.

**1. Funding is via prefunded genesis accounts, not (only) the faucet.** This
is a standard Kurtosis `ethereum-package` devnet: its genesis prefunds the
well-known 21-account set (mnemonic path `m/44'/60'/0'/0/n`), keys published in
`ethereum-package`'s `genesis_constants.star`. All 21 currently hold ~1e9 test
ETH (verified live); the faucet is just account #1. So a prefunded key funds
transactions directly, no faucet needed.

**2. The faucet is stuck because it underprices, not because inclusion is
broken.** The faucet's transfer (tx `0x8894…0581318`, type-2, `maxFeePerGas`
≈ 0.001 gwei) stayed pending indefinitely while the chain produced blocks. A
properly-priced transaction is included immediately: a self-verify value
transfer at 1 gwei priority mined on the first try (tx `0xbcc1…ccbd54f`,
block 18827, a type-0x06 frame receipt with the Transfer log). That probe was
sent from a faucet-funded key, which worked only because a 21k-gas transfer's
worst-case cost fit its ~0.001 ETH balance; the same key later produced
finding 4's false alarm. The fix for the faucet is to raise its gas price.

**3. `eth_estimateGas` / `eth_call` are unusable.** Any simulated execution
fails with `slot_number must be present in Amsterdam+ blocks` (the simulated
context lacks the consensus slot that real mined blocks carry). Every tx must
therefore be sent with an explicit `--gas-limit` (to skip estimation), and
state must be read from receipts/events rather than `eth_call`. The frame-tx
tooling here already does this; general dapp tooling (wallets, `forge`
scripting) will trip over it.

**Update (2026-07-07): resolved.** `eth_call`/`eth_estimateGas` carry the slot
and work again after the 2026-07-06 update, and ethrex commit `e7e495f` adds
`ethrex_simulateFrameTransaction` (a new `ethrex_` namespace), the frame-native
dry-run those flat-call methods structurally cannot express. It runs the
mempool validation prefix and, if it passes, a read-only multi-frame execution
at head, returning validity, the prefix shape, the resolved payer, the max
cost, and total/per-frame gas, bounded by the same 2^24 per-tx cap as
`eth_estimateGas`. `pool_frametx.py` now dry-runs every spend before sending:
it aborts one the node reports invalid (surfacing the violation), prints the
resolved payer, and sizes the uncapped SENDER frame from the simulated gas
rather than over-reserving. Confirmed live against `rpc1` (v17.0.0): a shield
returns `valid`, `SelfVerify`, the self-funded payer, and a SENDER frame of
~1.275M gas, so the frame is sized to ~1.59M instead of the hardcoded 10M. The
tool degrades to the fixed limits on an endpoint that does not enable the
`ethrex_` namespace (`-32601`).

**4. Plain contract creation works; the "no deploy path" conclusion was a
funding artifact.** The morning's evidence chain (creation tx reverting at
816k/1M gas, tiny creations pending forever, `DeployInstalledNoCode` from the
deploy frame, no CREATE2 factory) led to "there is no public path to deploy a
standalone application contract." That was wrong, for two stacked reasons:

  - **The deployer key was faucet-funded, not prefunded.** `devkey.txt` held
    0.0009 ETH, not the ~1e9 of a genesis account. The mempool admitted a
    200k-gas creation (admission checks `gas_limit × max_fee ≤ balance` at
    3 gwei ≈ 0.0006 ETH, just under), but the payload builder skipped it on
    every block, and non-frame txs that fail to apply are popped for the round
    yet **kept pooled**, so the tx pends forever with no error surfaced
    anywhere. A 1M-gas retry failed admission outright ("Account does not have
    enough balance"), which is what finally exposed the cause. The same tiny
    creation from a genesis-prefunded key (`gc.star` account #0) mined with
    status 1 on the first try.
  - **The morning's 1M-gas revert was out-of-gas in disguise.** Under
    EIP-8037, receipt `gasUsed` includes the state dimension: measured across
    three successful deploys, creation costs ~214k base plus **~1,545 gas per
    byte of runtime code** (about 7.7x the 200/byte deposit), so RecentRoots
    (1,122 bytes) costs 1.95M, not the ~350k a Sepolia intuition predicts.
    816k of a 1M limit was simply the point where it died.

  Two real deployment constraints survive the correction: the EIP-7825 per-tx
  gas cap of 2^24 = 16,777,216 (a 25M-gas submission is rejected with
  "Transaction gas limit exceeds maximum"), which at ~1,545 gas/byte caps a
  single deploy at roughly **10.7KB of runtime**; and `eth_estimateGas` being
  unusable (finding 3), so every deploy needs an explicit gas limit. The
  19.5KB `PoseidonBN254` library exceeded the cap (OOG at 16.59M) and was
  split into `PoseidonT3`/`PoseidonT4` (7,027 and 9,291 bytes, compiled with
  the legacy-codegen `libsmall` profile because via-IR's dispatch glue adds
  ~3KB), differential-tested against the same circomlibjs vectors, and
  deployed separately. `split_poseidon.py` in `contracts/` performs and
  documents the split.

**5. The restricted-state validation rules are mempool policy, not
consensus.** The `FrameSimViolation` rules that the earlier probes hit
(`StateWriteOutsideDeploy`, `DeployInstalledNoCode`, banned opcodes,
non-sender `SLOAD`) live in `validation_observer.rs`, which describes itself
as "a local peer policy, never a consensus rule": it is active only during
mempool simulation of the validation prefix. Deploy frames are additionally
near-vacuous on this implementation (the draft EIP's `SETDELEGATE` does not
exist in ethrex's opcode set, so a fresh EOA cannot actually satisfy
"leaves the sender with code"). None of this constrains what an included
block may do, and none of it affects the pool, whose state changes all happen
in the uncapped post-approval SENDER frame.

The remaining useful asks for the devnet authors, in order: (a) fix the
faucet gas price, and make the builder's skip-reason observable (a
perpetually-pending tx with no diagnostic is what turned a funding mistake
into a day of misdiagnosis); (b) make `eth_estimateGas` work under the
simulated-slot context; (c) add TXPARAM selectors for `nonce_keys` and
`recent_root_references` (gaps 1–2 above) so the envelope bindings become
provable in-EVM; (d) document the EIP-8037 deploy cost (~1,545 gas/byte of
runtime) and the 2^24 per-tx cap, which together bound single-tx deploys at
~10.7KB of runtime, or predeploy a CREATE2 factory so larger contracts can
be chunked.

## The integration: pool as a frame-native application

Because the pool's proof is a Groth16 SNARK and a devnet SENDER frame
executes real EVM, the proof is verified **onchain via the pairing precompiles inside the pool
call**. No attester, no missing verifier: the whole spend, proof check
included, is an ordinary application frame. That is the end-to-end story the
three EIPs were supposed to enable, and here it needs no extra trusted party.

`pool_frametx.py` builds each interaction as a type-0x06 frame tx:

```
shield:   [ VERIFY(target=sender, self-verify sig -> APPROVE),
            SENDER(target=pool, value=amount, data=shield(inner)) ]
transfer: [ VERIFY(target=POOL_SENDER, self-verify),
            SENDER(target=pool, value=0, data=transfer(Spend)) ]  # proof verified in-frame
withdraw: same, data=withdraw(Spend, recipient)
```

The VERIFY frame is the reference self-verify pattern (the sender's default EOA
code checks the envelope signature and approves execution+payment). The SENDER
frame runs the pool call as `tx.sender`; for spends the frame sender must be
the pinned `POOL_SENDER`, which is exactly the pool's pinned-sender binding.
The pool keeps its own NonceManager and RecentRoots for the two bindings the
devnet cannot yet expose (gaps 1 and 2), so the four VERIFY bindings split into
"verified in-EVM" (proof, pinned sender via the SENDER frame, operation shape)
and "kept in the pool contract" (spent set, root source) rather than being read
from the envelope.

### Validated offline before the run

- Envelope: `python3 frametx.py` runs the encoder's golden-vector self-check
  (RLP + sig_hash).
- Pool calldata: `cast` encodes the nested `Spend` tuple; selector
  `0xe60330a3` and the fixture's `nf1` were confirmed present by inspecting
  the built calldata.

Both layers were then exercised live end to end (next section).

### `--protocol-nonces` (the EIP-8250 spent-set experiment)

`pool_frametx.py --protocol-nonces` builds the transfer/withdraw with
`nonce_keys = sorted([nf1, nf2])`, `nonce_seq = 0`: the variant where the two
nullifiers ARE the protocol keyed-nonce set, so the devnet's NONCE_MANAGER
becomes the pool's spent set and same-note/replay protection is enforced by the
protocol. It is expected to be rejected by the public RPC (mempool admits only
`[0]`) and, even accepted, is unbound in-EVM (gap 1). It is included to
document the intended faithful mapping and to test it the moment a builder
endpoint is available.

### Proof placement and FOCIL eligibility

Two spend shapes matter here, and they sit on opposite sides of EIP-8141's
100k mempool cap.

**Today's shape: proof in the SENDER frame.** The Groth16 verify over eight
direct public signals (~243k) cannot fit the 100k VERIFY prefix budget, so
this build checks the proof in the execution frame. That placement
is sound for this build because the spent set is contract state consumed in
the same frame: an invalid proof reverts the whole pool call and consumes
nothing. The transaction is trivially Profile 2 eligible under the FOCIL
eligibility draft (the prefix is an 80k self-verify reading only sender
state), so FOCIL can enforce inclusion of the envelope. What the envelope
cannot express in this shape is spend validity: an included-but-reverting
spend satisfies the inclusion guarantee, and a paymaster cannot condition
payment on the proof.

**The faithful shape: proof in the VERIFY prefix.** Once the nullifiers become
EIP-8250 keys consumed at payment approval, the proof must be checked before
APPROVE: key consumption survives later-frame reverts, so an execution-frame
proof check would let an invalid spend burn its nullifiers. That prefix costs
roughly 0.25M gas, over the 100k public-mempool cap but comfortably under the
FOCIL eligibility draft's `MAX_VERIFY_GAS_PER_TX = 2^20` (~1.05M). This is
exactly the draft's separation: public mempool admission and FOCIL
eligibility are different policies, and this transaction reaches includers
through a custom mempool or direct submission. The measured verification work
is ~23% of the FOCIL draft's verify budget (the earlier Poseidon-compressed
claim design measured ~62%; binding the nine publics directly in the verifier
removed all Poseidon hashing from VERIFY). The FOCIL draft charges static
declared prefix gas, not gas used, so the current 20k self-verify plus 300k pay
frame is budgeted closer to 320k plus signature cost. Tightening declared frame
gas is what converts the measured cost into more IL capacity.

**Enforcement is the index-based check.** FOCIL's stock omission rule
(end-of-block nonce and balance) cannot express keyed-nonce validity. The
mechanism that fits is the builder-claimed-index approach from the
FOCIL-native-AA thread: the builder names the block index where insertion was
attempted, attesters reconstruct state at that index (EIP-7928 BAL) and replay
the validation prefix, and "another transaction consumed this key first"
becomes a verifiable omission justification.

**The VOPS surface is the remaining constraint.** Profile 2 bounds prefix
state reads to protocol validation state plus the first `AA_VOPS_SLOT_COUNT`
storage slots of `sender` and `payer`. The pool's own NonceManager and
RecentRoots keep their state behind mappings, at keccak-derived slots outside
that surface, so moving today's contract-side checks into the prefix would not
be eligible. The faithful mapping is what fixes this: keyed nonces and
recent-root references are protocol validation state, inside the surface by
definition, read from the envelope via the TXPARAM selectors of gaps 1 and 2.
Those selectors are therefore not only what turns the trusted bindings into
proven ones; they are what makes the spend FOCIL-enforceable.

**What is done on the pool side, assuming the devnet upgrades.** The proof
check is now factored so the faithful shape is ready the moment the devnet
raises `MAX_VERIFY_GAS` and exposes the `nonce_keys` selector, and the split is
tested (contracts/test/ShieldedPool.t.sol):

- `ShieldedPool.verifySpend(Spend) view` is the whole spend validity check
  (non-zero nullifiers, EIP-8272 recent-root binding, and the Groth16 proof
  over the nine public signals, bound directly), and nothing else. It is
  `view`, so it runs under `STATICCALL`, which is what a VERIFY frame is: a
  paymaster targets it, staticcalls `verifySpend`, and `APPROVE`s payment if
  it returns. A test asserts it succeeds under a raw `staticcall` (proving no
  state writes). It deliberately omits the pinned-sender check, since a
  VERIFY frame runs as ENTRY_POINT rather than `tx.sender`; that binding stays
  in the SENDER-frame path.
- `ShieldedPool.checkKeySet(Spend, bytes32[] nonceKeys) pure` is the EIP-8250
  spent-set binding as a pure function: it asserts the transaction's
  `nonce_keys` are exactly the two proven nullifiers, distinct and sorted. The
  `ProofPaymaster` below now enforces this same rule in-VERIFY via the
  `NONCEKEYLOAD` (0xB9) opcode, turning the binding from trusted into proven.
- The `APPROVE`-emitting paymaster exists and is deployed as `ProofPaymaster`
  (`ProofPaymaster.yul`, generated by `paymaster.py`; Yul, since no compiler
  emits `APPROVE`/`NONCEKEYLOAD`). In the pay VERIFY frame it (1) binds the
  spent set, requiring `len(nonce_keys) == 2` (TXPARAM 0x0D) and
  `nonce_keys == sorted{nf1, nf2}` (NONCEKEYLOAD 0xB9); consensus enforces
  strictly-increasing keys, so the match fixes the exact set and rejects
  `nf1 == nf2` (the same-note attack); (2) STATICCALLs `verifySpend` for the
  proof/root/canonicity; (3) `APPROVE`s payment (scope 0x1, which does not
  require `frame_target == sender`) only if both hold. Structure, funding
  (`receive()` guard), and the revert paths are verified on anvil; the 0xB9
  binding executes only in a builder-included multi-key tx (see below).
  `pool_frametx.py --proof-in-verify` builds the transaction, setting
  `nonce_keys = [nf1, nf2]` so the binding has something to check.

The 2026-07-06 devnet update below has since landed the multi-VERIFY grammar
and the `NONCEKEYLOAD` / `RECENTROOTREFLOAD` accessors. What remains is split:
Ethrex-side public admission policy, verify budget, and VOPS-aware observer
rules; pool-side root-reference wiring and settle-only hardening. Once those
are in place, the SENDER frame shrinks to state changes: append the two output
commitments and pay out, with the nullifiers already consumed as protocol
keyed nonces at approval.

**Measured gas split** (standard EVM, `forge test --mt test_gas_frame_split`),
per operation, VERIFY frame (the proof check) vs exec frame (state changes):

| operation | VERIFY frame (proof) | exec frame (state) | full |
|---|---:|---:|---:|
| shield   | none (no proof)     | ~1.20M | ~1.20M |
| transfer | ~246k               | ~889k  | ~1.13M |
| withdraw | ~243k               | ~957k  | ~1.20M |

VERIFY is `verifySpend` plus `checkKeySet` (~3k): the Groth16 pairing check
plus one scalar mul per public signal (nine signals, ~6.2k each) and the
recent-root staticcall. There is no Poseidon hashing in VERIFY: the nine
publics are bound directly by the verifier rather than compressed into a
claim the contract recomputes, which cut this column from ~648k. It sits at
~23% of the FOCIL draft's `MAX_VERIFY_GAS_PER_TX = 2^20` (~1.05M) as measured
work, but FOCIL budget fill is based on declared prefix gas. With today's 20k
self-verify plus 300k pay frame limits, the static budget cost is closer to
320k plus signature cost. Both the measured proof work and the declared-prefix
budget exceed EIP-8141's 100k public-mempool cap, so a proof-in-VERIFY spend
needs a custom mempool or direct submission until admission policy changes.
The exec frame is dominated by the Merkle hashing: the two frontier inserts
share one 20-level root recompute (~21 Poseidon hash2, ~0.73M); shield is one
insert plus the same root recompute and carries no proof.
About 50k of the exec figure is today's keyed-nonce consumption, which moves to
the approval side in the faithful shape. These are vanilla-EVM numbers, and
EIP-8037 changes only the exec column: the state dimension charges writes
exclusively (`cost_per_state_byte = 1530`, so a fresh storage slot is
64 × 1530 = 97,920, a new account 120 × 1530 = 183,600, code deposit
1,530/byte; `gas_cost.rs`), while reads, hashing, precompiles, and calldata
stay regular-dimension. A static VERIFY frame cannot write state, so its
column carries over essentially unchanged. The live runs confirm this to the
gas (the deployed contract predates the batched root recompute and the
direct public signals, so its vanilla baseline is the older ~2.21M transfer):
the transfer's +658k over vanilla matches its ~6 fresh storage slots
(2 leaves, 2 root-ring entries, 2 nonce slots ≈ 588k) plus rewrites, and the
two live withdraws differ by exactly 183,600, the one-time new-account charge
for the recipient's first payout. Inlining Poseidon does NOT cut the exec
column: measured, internal linkage loses 35-80k per spend depending on
codegen, because the constant tables then pay quadratic memory expansion
inside the large exec frame instead of near-linear cost in throwaway
DELEGATECALL sub-frames (warm DELEGATECALL overhead is only ~0.2-0.6k per
call). The external-library split stays. VERIFY carries no Poseidon and sits
~60k over the Groth16 4-pairing floor (45k + 4 x 34k = 181k of precompile
cost, EIP-1108), which no Groth16 variant can beat on today's EVM.

## The live run (2026-07-03)

The full flow ran end to end on the public network, deployed from genesis
account #0 with explicit gas limits (finding 3) at 1 gwei priority:

| contract | address | deploy gas |
|---|---|---|
| RecentRoots | `0x9ECB6f04D47FA2599449AaA523bF84476f7aD80f` | 1,947,298 |
| Groth16Verifier | `0x4Af231e5E624038Cd40FC4fd5b86B39d13E1429e` | 1,921,111 |
| PoseidonT3 | `0xB0275c9A863072599ea283A143414370470a369a` | ~11.1M |
| PoseidonT4 | `0xC5FC7cE1d859E6604f1e8E57BA0f4A92858850Bc` | ~14.6M |
| ShieldedPool | `0xB29dB8A6b1C596B64f7E1dD5358d59Db73648E17` | tx `0xf435b6b8…70ded7` |
| NonceManager | `0x9f938cBfADF0633ddBA0e116F0D1D0e2a90F8E97` | created by the pool |

Then the three frame transactions, each a type-0x06 with the 80k self-VERIFY
prefix and a 10M SENDER frame (EIP-8037 accounting inflates execution gas too,
so the Sepolia-sized 3M frame would not have been safe):

- **shield** 1 ETH: tx `0xd0277767…65786b`, block 19388, status 1, 1,502,420
  gas. The `LeafAppended` root equals the fixture's transfer root, so the
  split onchain Poseidon matches circomlibjs exactly, and RecentRoots
  recorded it at slot 19388.
- **transfer** (join-split, Groth16 verified onchain in the SENDER frame):
  tx `0xd329f3fd…52a8cc`, block 19394, status 1, 2,871,428 gas. The two
  output notes' final root equals the fixture's withdraw root, recorded at
  slot 19394.
- **withdraw**: tx `0xace5950f…3f0c5a`, block 19401, status 1, 3,065,907 gas.
  The recipient `0x…cafebabe` ends holding exactly the proven public amount,
  0.55 ETH.

The slot values ride in as `_slot_transfer` / `_slot_withdraw` in
`deploy_config.json` (the receipt block numbers, since `eth_call` cannot read
the pool's state here). `frametx.py` is the envelope encoder, `run_live.sh`
the deploy driver, and `deploy_config.json` in this directory holds the live
addresses.

## The live run (2026-07-05): the optimized pool, and the faithful-shape attempt

A fresh deployment of the optimized contract (direct public signals, batched
Merkle append; the 2026-07-03 addresses run the old code) plus the first
onchain attempt at the proof-in-VERIFY shape. Same procedure: genesis-funded
deployer, explicit gas limits, 1 gwei priority. `deploy_config.json` now
records this deployment.

| contract | address |
|---|---|
| ShieldedPool | `0x303CB317624c74bB20Acbb9E13c8D745C6379826` |
| RecentRoots | `0x0EeC8BC5B2A3879A9B8997100486F4e26a4f299f` |
| Groth16Verifier | `0x34fa02cf467232c201FB9E90c786A69c7d743D8D` |
| PoseidonT3 / T4 | `0x48b90E15…12b361` / `0xF01ecC1d…5BBfb` |
| paymaster (proof-gated APPROVE) | `0xa4fd91b3…af9ab5` |

The standard flow confirms the optimization live, with every root matching
the fixture (so the new circuit, contract, and wallet agree onchain):

- **shield**: tx `0x231b3fdb…bb6cfd`, block 50172, status 1, **1,370,163**
  gas (was 1,502,420).
- **transfer**: tx `0x7f2be746…619234`, block 50269, status 1, **1,737,843**
  gas (was 2,871,428, a 39% cut; the batched append also removes most of the
  EIP-8037 fresh-slot writes).
- **withdraw**: tx `0xb2aa6899…1dbae9`, block 50274, status 1, **1,793,388**
  gas (was 3,065,907). The pool ends holding exactly the unspent change note.

**The faithful-shape attempt was admitted, never mined, evicted.** The first
`--proof-in-verify` transaction (self-verify frame + paymaster VERIFY frame +
SENDER frame) got no error and no inclusion. A bisection with a 6-byte probe
suggested the builder dropped any two-VERIFY-frame prefix. That conclusion was
wrong, and the 2026-07-06 devnet update below corrects it: the probe used the
wrong frame flags (two execution-scoped frames) and, decisively, an unfunded
paymaster. The real rule is the EIP-8141 `[only_verify, pay]` grammar with a
funded pay-frame target.

## The devnet update (2026-07-06): multi-VERIFY works; only the budget remains

The devnet advanced to ethrex v17.0.0 (commit `9d06722`) with four commits
that land on our blockers:

- **`5c16ea3`** — the mempool now admits a pay frame targeting a paymaster
  `P != sender`; the target-must-equal-sender check was correctly narrowed to
  only execution-scoped (`0x02`) VERIFY frames.
- **`9d06722`** — a trustless paymaster demo (`OpenSponsor.yul` +
  `frametx_sponsor_submit.py`) assembling the canonical
  `[only_verify(sender), pay(sponsor), sender(recipient)]` shape.
- **`587051`** — **`NONCEKEYLOAD` (0xB9)**: an opcode reading `nonce_keys[i]`
  (stack `[index] -> nonce_keys[index]`, gas 3, halt on out-of-range), and the
  commit notes it mirrors **`RECENTROOTREFLOAD`**, so the recent-root-reference
  accessor exists too. These are the two TXPARAM-style selectors the earlier
  "two capability gaps" section said were missing; both now exist.
- **`aecb138`** (branch tip, one ahead of the deployed node) — confirms
  `MAX_VERIFY_GAS = 100_000` and shrinks the demo's prefix frames to 20k + 40k.

Re-tested against this node:

- **The multi-VERIFY grammar mines.** Deploying `OpenSponsor`, funding it, and
  submitting `[only_verify(0x02, sender), pay(0x01, sponsor, verify()), SENDER]`
  mined at block 68007, status 1, with the **sponsor as payer** (it paid the
  gas; the sender paid only the transferred value). The earlier "silent drop"
  was self-inflicted: an unfunded pay-frame target reverts the prefix, and the
  pay-frame target is the payer, so it must hold ETH (OpenSponsor's
  `receive()` guard exists exactly to be fundable).
- **`eth_call` works again** (commits `70874d6`/`8dccd22` default a missing
  `slot_number` to 0), un-breaking the reads that finding 3 documented.
- **The `nonce_keys[0]`-only mempool policy is the operative wall.** Submitting
  the faithful spend (which sets `nonce_keys = [nf1, nf2]`) is rejected at
  admission: `only key-0 frame transactions are admitted to the public
  mempool`. With `nonce_keys = [0]` instead, the rejection is the budget error
  `prefix gas budget (frames + sig cost) exceeds MAX_VERIFY_GAS` (the 300k pay
  frame holding the ~243k `verifySpend` busts the 100k cap). So three
  requirements gate a faithful spend, and all three point to the same missing
  piece, a builder-direct submission path: the non-zero `nonce_keys`, the
  paymaster's observer-banned `STATICCALL`, and the verify budget.

**Update (2026-07-08): two of the three gates cleared, one remains, now
pinned.** The devnet update raised `MAX_VERIFY_GAS` (~100k to ~500k) and now
admits non-zero `nonce_keys`, both confirmed live through
`ethrex_simulateFrameTransaction` (a 300k VERIFY prefix and a non-zero keyed
nonce each pass admission read-only), and it derives the EIP-8272 slot EL-side
(`slot = (block_timestamp - genesis_timestamp) / seconds_per_slot`, a
`derivedSlotTime` knob, commit `939ece2`) so root writes no longer land at slot
0 without a CL change. Dry-running the full faithful `--proof-in-verify`
transfer through the simulator now returns the `OnlyVerifyPay` shape and a
single violation, `StorageReadNonSender`: the ERC-7562 observer bans the
paymaster's `verifySpend` from reading the pool's RecentRoots and tree storage
(neither the sender's nor the payer's). That is the sole remaining gate, and it
is exactly the VOPS "validation reads" allowance still to be deployed; a
builder-direct path is no longer required once it lands. `pool_frametx.py`
gained a `--dry-run` that reports this through the simulator.

**Pool-side, the faithful shape is built and the spent-set binding is now
proven, not trusted.** `ProofPaymaster.yul` (generated by `paymaster.py`) is
the pay-frame payer: it binds `nonce_keys == sorted{nf1, nf2}` via
`NONCEKEYLOAD` (0xB9) and `len` via TXPARAM 0x0D, STATICCALLs `verifySpend`,
and `APPROVE`s payment only if both hold. It deploys and funds on the devnet
(`receive()` guard), and its structure and revert paths are validated on anvil.
The 0xB9 read itself resolves only for `index > 0` in a builder-included
multi-key tx, so like the rest of the faithful spend it awaits a builder-direct
path. `pool_frametx.py --proof-in-verify` assembles the whole thing.

## The faithful spend runs live through the public mempool (2026-07-09)

The last gate was pool-side, not ethrex-side, and closing it needed no
builder-direct path and no VOPS "validation reads" allowance. Two changes made
the pay VERIFY frame observer-clean, so the full faithful spend now submits
through the public `eth_sendRawTransaction` and mines.

**1. No non-sender storage read.** `verifyProofOnly` is `verifySpend` minus the
`roots.check`. It checks the proof, field canonicity, and value range, all over
calldata and the code-resident verification key, so it reads no storage. The
paymaster STATICCALLs it instead of `verifySpend`, clearing the observer's
`StorageReadNonSender` ban. Root recency is not dropped, only moved: the proof
binds `root` as a public signal, and the SENDER frame still runs `verifySpend`
(via `_spend`), so a stale or fabricated root reverts in execution and consumes
only the proven nullifiers. `ProofPaymaster.yul` also stopped reading storage
for its own pool address: the constructor appends the address to the deployed
code and the runtime reads it back with `CODECOPY`, so the paymaster is
`SLOAD`-free.

**2. No `GAS` opcode in the validation trace.** With the SLOAD gone the
simulator surfaced the next violation, `BannedOpcode(90)` = `GAS` (`0x5a`). The
observer bans `GAS` in validation frames, and it traces through the STATICCALL,
so the ban reaches the snarkjs `Groth16Verifier`, whose precompile calls use
`staticcall(sub(gas(), 2000), ...)`. Three sites were changed to forward a
fixed gas literal instead: the verifier's precompile calls (`sub(gas(), 2000)`
to `30000000`), the pool's `verifier.verifyProof{gas: 30000000}(...)`, and the
paymaster's STATICCALL (a `0xffffffffffffffff` literal). The EVM caps each at
63/64 of remaining gas, so nothing is undersized, and the proof math is
unchanged (44 forge tests pass).

Redeployed the verifier, pool, and paymaster (RecentRoots and the Poseidon
halves reused), shielded a fresh note, then dry-ran and sent the faithful
transfer:

- dry-run: `valid=True`, `violation=None`, `OnlyVerifyPay`,
  gas `2,025,994` (self-verify frame 0, pay frame 282,427, SENDER frame
  1,706,654). The pay frame fits `MAX_VERIFY_GAS = 500k`.
- live transfer `0xabcc9c82…` mined at block 104569, `status=1`, gasUsed
  2,025,994, submitted through the public endpoint (no builder-direct).
- payer = the paymaster: its balance dropped by exactly the gas cost, so
  `APPROVE` fired only because the proof and the `nonce_keys == {nf1, nf2}`
  binding both held.
- tree settled: `currentRoot` advanced to the fixture's post-transfer root.
- replay refused: a second `--proof-in-verify` transfer of the same notes is
  rejected at admission with `Nonce mismatch: expected 1, got 0`, so the two
  nullifiers were consumed as protocol keyed nonces (EIP-8250), not double-spent.

This is the first proof-in-VERIFY private spend mined through the public
mempool: the proof is verified inside the payment VERIFY frame, the payer pays
only on a valid proof, and the spent-set binding is proven by `NONCEKEYLOAD`,
not trusted. The one remaining coarseness is the redundant second `verifySpend`
in the SENDER frame (the settle-only entrypoint item below).

### Security review: the paymaster must bind the calldata selector (2026-07-09)

A review pass found a real bug in the first faithful-spend paymaster. It read
`nf1`/`nf2` at fixed calldata offsets and STATICCALLed the pool, but checked
only that the call did not revert, never that the calldata was actually
`verifyProofOnly(Spend)`. So an attacker could target the paymaster with
calldata whose selector is any non-reverting, storage-free pool view, set the
two words at bytes 68 and 100 to match the transaction's `nonce_keys`, and the
paymaster would `APPROVE` payment with no proof at all. `ctxFor(address)` (pure,
never reverts, reads no storage, so it also clears the observer's
`StorageReadNonSender` ban) is a working key: dry-run against the first live
paymaster returned `valid=True, payer=<paymaster>, violation=None`. The
paymaster would sponsor arbitrary unproven transactions, draining it.

The fix binds the pay-frame calldata to exactly `verifyProofOnly(Spend)`:
selector `0xe5367e41` and the full static 548-byte encoding, both checked before
the STATICCALL. Redeployed and confirmed live: the `ctxFor` and `currentRoot`
attacks now come back `valid=False` (`validation prefix frame reverted`), while
the legit faithful transfer still mines (tx `0xc2555b29…`, block 111029,
2,026,024 gas, payer = paymaster, tree settled to the fixture post-transfer
root). The selector is fixed by the `Spend` tuple shape; `paymaster.py`
regenerates the bytecode if either changes.

The same pass hardened `pool_frametx.py`: it sized the SENDER frame from the
simulation but never checked the SENDER frame *succeeded*, so a transfer sent in
the same block the shield published its root (where `roots.check` sees
`current == slot` and reverts `RootNotRecentForPool`, referenceable only from
the next block) was sized to that cheap revert and ran out of gas live. It now
refuses to send when the simulation's `executionStatus` is not `success`.

Two findings for ethrex, not the pool: (1) a mined-but-reverted frame
transaction consumes its keyed nonce (as a classic reverted tx consumes its
account nonce), so a spend that reverts after approval permanently burns its
nullifiers, the real-world sharp edge behind the "post-approval revert" note in
`_settle`; (2) `ethrex_simulateFrameTransaction` returned `valid=True` for a tx
that admission then rejected on a keyed-nonce mismatch, a sim/admission
inconsistency. Dora also does not index type-0x06 frame transactions, so the
spend is invisible in the explorer even though it mined.

### Security review: the paymaster must bind the sender, not just the proof (2026-07-10)

The selector fix above closed the "APPROVE with no proof" path, but a deeper
one remained, and it is the more important finding. **A valid spend proof is
costless to mint.** The circuit's zero-value dummy input (a legitimate feature,
it is how a single note is spent through the two-input circuit) lets anyone
build a fully valid proof with `in_value = [0, 0]`: conservation forces every
output, `publicAmount`, and `fee` to 0, membership is skipped for both inputs,
`root`/`ctx` are free, and the two nullifiers are fresh, distinct, non-zero
Poseidon outputs. So `verifyProofOnly` passing is **not** evidence of ownership
or of any value moving. The faithful paymaster bound the proof and the
`nonce_keys` set but authenticated neither the sender, nor `nonce_seq == 0`, nor
the frame shape, nor the SENDER frame it pays for. EIP-8250's own Security
Considerations require the opposite: a single-use-key application MUST
authenticate `(sender, nonce_keys_hash, nonce_seq == 0)` in VERIFY. So, opened
beyond operator-only builder-direct submission, anyone could mint a dummy proof,
set `nonce_keys = sorted{nf1, nf2}`, and have the funded paymaster sponsor their
transaction, draining it (and, since the SENDER frame was unbound, sponsoring
arbitrary execution). It was a *trusted* paymaster, safe only because the
operator was the sole submitter, not the *trustless* one the docs claimed.

The fix adds the missing envelope bindings to `ProofPaymaster.yul`, checked
before APPROVE: `TXPARAM(0x02) == POOL_SENDER` (only the pinned sender may be
sponsored), `TXPARAM(0x01) == 0` (nonce_seq, the EIP-8250 single-use rule), and
`TXPARAM(0x09) == 3` (the exact `[self-verify, pay, sender]` shape, no extra
payer-funded frames), alongside the existing `len(nonce_keys) == 2` and
`nonce_keys == sorted{nf1, nf2}`. **The sender bind is the load-bearing one for
this pool.** Because the pool pins `POOL_SENDER` everywhere (`_spend` reverts
otherwise), only `POOL_SENDER` can produce an acceptable spend; binding
sponsorship to that same pinned sender means a third party cannot forge
`POOL_SENDER`'s signature and so cannot get sponsored, which closes the drain.
`POOL_SENDER` is now a second 32-byte constructor arg (`pool || poolSender`
appended to the code; still SLOAD-free, read via CODECOPY at codesize-64 and
codesize-32). The honest faithful transfer still satisfies every check (sender
`POOL_SENDER`, `nonce_seq = 0`, three frames).

Status: recompiled with `solc 0.8.30 --strict-assembly --optimize --optimize-runs
200`, the bytecode disassembled with every binding confirmed present and ordered,
and re-run live on the Hegotá devnet (chain 3151908, blocks ~119310-119323) on a
fresh stack whose `POOL_SENDER` is a throwaway operator (the sender's identity is
irrelevant to the binding logic). Three checks:

- **deploy / bytecode match**: `run_live.sh` deployed the hardened paymaster and
  its on-chain code equalled `paymaster.py --runtime pool poolSender` (the
  script's own line-63 assertion passed).
- **positive (no regression)**: the honest `--proof-in-verify` transfer signed by
  `POOL_SENDER` mined (tx `0x874e9fd6…`, block 119323, status 1, 2,124,052 gas),
  the **payer was the paymaster** (its balance dropped by the gas cost, so APPROVE
  fired only because the new binds passed), both nullifiers show
  `NONCE_MANAGER seq = 1`, and the root advanced to the fixture withdraw root.
- **negative (drain closed)**: the identical transaction signed by a
  non-`POOL_SENDER` key was rejected by `ethrex_simulateFrameTransaction` in the
  **validation prefix** (`valid=False, payer=None, executionStatus=None,
  violation="validation prefix frame reverted"`), i.e. the pay frame reverted on
  the `TXPARAM(0x02)` sender bind *before any approval*, not merely in SENDER-frame
  execution. The only variable between the two runs is the signer, and the only
  sender-dependent prefix check is that bind, so the rejection is attributable to
  it. (The `nonce_seq != 0` and four-frame rejections are not reachable through
  the stock `pool_frametx.py`, which always builds `nonce_seq = 0` and three
  frames; those two remain verified structurally by the disassembly, not live.)

Two items deliberately left open. (1) **SENDER-frame binding.** The paymaster
still does not bind the SENDER frame's `(target, keccak256(calldata))`; it relies
on the sender pin instead, which suffices for this single-operator pool. A
*permissionless* variant (no sender pin, anyone may spend) additionally MUST bind
the SENDER frame, via per-frame introspection: canonical EIP-8141 exposes this
through `FRAMEPARAM (0xb3)` + `FRAMEDATALOAD (0xb1)`, and ethrex's build through
per-frame `TXPARAM` selectors `0x11–0x15` (REVIEW opcode list above). The
capability exists in the spec and on the devnet; the exact per-frame stack ABI on
the current build is not pinned in-repo and is unexercised, so that variant is
specified but not implemented. This was never an EIP or an ethrex *capability*
gap, only unused capability in the paymaster. (2) **Fee routing.** The pool pays
`fee` to `msg.sender`, which in this shape is `POOL_SENDER`, not the payer (the
paymaster); for the single-operator pool that is the same entity that funds the
paymaster, so the loop nets out, but a distinct third-party payer would need the
pool to route the fee to the payer (a payer `TXPARAM`, not currently exposed).

### Conservative v2 validated live, and two findings (2026-07-11)

The archived v2 statement (domain-separated nullifiers, in-circuit `nf1 != nf2`,
pull-credit settlement, envelope-bound fee recipient, and reproducible paymaster)
ran end to end on a fresh public-devnet stack (chain 3151908, blocks
134171-134180) with a throwaway operator. The whole
point was to exercise the new SENDER-frame binding live for the first time: the
paymaster now also binds the pay-frame and SENDER-frame shape with `FRAMEPARAM`
(0xb3) and `FRAMEDATALOAD` (0xb1), which no prior run touched. It works. The honest
`--proof-in-verify` transfer mined (tx `0x91abb6a5…`, payer = paymaster, pay frame
291k gas, up from ~283k, consistent with the added per-frame reads), the two
nullifiers show `NONCE_MANAGER seq = 1`, the root advanced to the fixture withdraw
root, `pool.domain()` equals the proof's `domain` public signal (deployment-bound
proof generation agrees with the contract's `domainFor`), and a non-`POOL_SENDER`
submitter is rejected in the validation prefix (`valid=False, payer=None`). So the
frame-introspection ABI the Yul assumed is empirically correct on ethrex.

Two findings:

1. **`run_live.sh` underfunded the paymaster deploy (fixed).** The v2 paymaster
   runtime is 532 bytes (the SENDER-frame binding roughly tripled it), needing ~820k
   code-deposit gas plus constructor under EIP-8037, ~1.04M total. The script's
   `--gas-limit 1000000` produced a codeless deploy, which the runtime-match guard
   then correctly flagged as a "mismatch". Raised to 2M; deployed code then equals
   `paymaster.py --runtime` byte-for-byte (532 bytes). The guard worked; the mismatch
   was the symptom.

2. **The faithful-shape fee was stranded (fixed).** Settlement credits `fee` to the
   fee recipient, which the paymaster binds to itself. But the paymaster is a passive
   contract (its only ETH-in path is a bare receive), and the old `claimFee` keyed the
   claim on `feeCredit[msg.sender]`, so the paymaster had no way to collect its own
   credit and no third party could push it out. Confirmed live: `feeCredit[paymaster]`
   held the fee with no path to recover it. Not a soundness bug (nothing lost or
   stealable), but the sponsorship loop did not close. Fixed by making the claims
   pushable: `claimWithdrawal(who)` / `claimFee(who)` now pay `who`'s own recorded
   credit to `who`, callable by anyone, so a keeper moves the fee into the paymaster's
   balance where it funds future sponsorship. Zeroed before the send, so a recipient
   that reverts on receipt only reverts its own claim. Forge-tested against a passive
   receiver pushed by a third party (48 tests pass).

## Local end-to-end run of the faithful spend (2026-07-06, patched node)

The faithful spend ran end to end against a local ethrex built from
`hegota-devnet` (tip `aecb138`), confirming our side works once the roadmap
below lands. Because every blocker is mempool policy and none is consensus,
the only changes were to the mempool-admission path, set to their
post-roadmap values (no consensus edit): raise `FRAME_TX_MAX_VERIFY_GAS`
100k -> 2M, admit non-zero (nullifier) keyed-nonce txs, and run the prefix
simulation with an inactive observer so the paymaster's `STATICCALL` is not
banned (all three gated on `ETHREX_ADMIT_NONZERO_KEYS`). Chain id 9, Hegota at
genesis, the `0x…8250`/`0x…8272` predeploys placed in the genesis alloc.

Deployed our full stack (pool, verifier, split Poseidon, ProofPaymaster) and
ran the flow:

- **shield**: block 85, status 1, 1,370,163 gas; the `LeafAppended` root equals
  the fixture transfer root.
- **faithful transfer** (`--proof-in-verify`, `nonce_keys = [nf1, nf2]`):
  tx `0xdc24f7ce…17335e`, block 111, status 1, 2,029,698 gas. Verified:
  the transaction's **payer is the ProofPaymaster** (it paid the gas), so
  `APPROVE`-at-payment fired only because the pay frame's checks passed; the
  `NONCE_MANAGER` predeploy records `seq = 1` for both `nf1` and `nf2`, so the
  two proven nullifiers were **consumed as protocol keyed nonces**; and the
  pool root advanced to the fixture withdraw root, so the outputs settled.
- **negative control**: the same spend with `nonce_keys = [1, 2]` (not the
  proven nullifiers) is rejected, the pay frame reverts in the prefix
  simulation. The `NONCEKEYLOAD` spent-set binding is load-bearing, not
  vacuous.

So the whole faithful shape executes against the real Hegota opcodes
(`NONCEKEYLOAD` 0xB9, `APPROVE` 0xAA, native keyed-nonce consumption): proof
checked in the VERIFY prefix before approval, spent set bound to the proven
nullifiers in-EVM, nullifiers consumed as protocol state, payment approved by
the proof-gated paymaster, outputs settled in the SENDER frame. The 2.03M gas
includes a redundant second `verifySpend` in the SENDER frame (roadmap item 8).
What remains is entirely the mempool/FOCIL work below, which is ethrex-side.

## Roadmap: from consensus-valid to mempool-admitted and FOCIL-eligible

**The key fact this rests on.** The faithful spend is already valid by
consensus. Every gate that rejects it, the key-0 rule, `MAX_VERIFY_GAS`, and
the ERC-7562 observer, lives in ethrex's mempool-admission path
(`Blockchain::validate_transaction` in `crates/blockchain/blockchain.rs`, and
`LEVM::simulate_frame_validation_prefix` in `crates/vm/backends/levm/mod.rs`,
whose own docstring says "local peer policy, never consensus"). The
block-execution path (`execute_tx_in_block`) enforces none of them. So a
builder that includes the spend produces a valid block. The goal is not to
route around the mempool forever, it is to make the mempool admit these
transactions and FOCIL enforce them. Builder-direct is the interim channel to
run and test the spend while that lands.

Why this is not a config flip: a nullifier is the archetypal keyed nonce, and
a nullifier key is a different restricted-state type from an account nonce. It
is single-use, unpredictable, its sequence is always 0 -> 1, there is no
replace-by-higher-seq, and two transactions racing to consume the same key is
the normal double-spend case, not an error. The mempool and FOCIL machinery
have to treat one-time keys on their own terms.

**Ethrex-side, for public-mempool admission:**

1. **Admit one-time / non-zero keyed-nonce transactions.** Lift the key-0-only
   rule (`blockchain.rs:3057`) and add pending/replacement tracking for
   single-use keys: first-seen-wins, no seq-replacement, and graceful
   de-confliction of racing spends. This is the substantive item, and it is
   what keyed nonces were built for.
2. **Rework the verify budget.** `FRAME_TX_MAX_VERIFY_GAS = 100_000`
   (`transaction.rs:1958`, EIP-8141 mempool rule) rejects a proof-carrying
   verify (~243k measured). Raise it, or give proof-in-VERIFY spends a distinct
   budget tier.
3. **Scope the validation observer to a VOPS surface.** The observer bans the
   paymaster's external `STATICCALL`, correctly for arbitrary state reads. The
   fix is not to disable it but to define a validity-only-peeking-state surface
   the spend's validation stays inside, so it is cheaply admittable without
   arbitrary calls. Depends on the bindings reading protocol state (items 6-7).

**Ethrex-side, for FOCIL eligibility:**

4. **Map the spend into the AA VOPS profile.** Prefix reads must be protocol
   validation state: keyed nonces (done, via `NONCEKEYLOAD`) and the recent
   root (via `RECENTROOTREFLOAD`). The pool's own NonceManager/RecentRoots sit
   at keccak-derived slots outside the surface; the faithful mapping replaces
   them. The current proposal is
   [EIP-8369](https://github.com/ethereum/EIPs/pull/12110).
5. **Index-based omission check.** FOCIL's stock end-of-block rule cannot
   express keyed-nonce validity. The fit is the builder-claimed-index approach:
   the builder names the insertion index, attesters reconstruct state there
   (EIP-7928 BAL) and replay the validation prefix, so "another transaction
   consumed this key first" becomes a verifiable omission justification, which
   is exactly the nullifier-race case.

**Pool-side (our remaining work):**

6. **Wire `RECENTROOTREFLOAD`.** Bind the spend's root to a protocol
   recent-root reference in the paymaster, the way the nonce-key binding is
   done, so the root binding is proven and inside the VOPS surface. The last
   trusted binding.
7. **Harden the paymaster binding.** Check `nonce_seq == 0`, pin the calldata
   selector to `verifySpend(Spend)`, and bind the expected SENDER-frame
   settlement data before approving payment. The current redundant SENDER
   re-verification makes the local run safe, but a settle-only path needs this
   binding before that redundancy is removed.
8. **Add a settle-only SENDER entrypoint.** In the faithful shape the protocol
   consumes the keyed nonces at approval and the proof is checked in VERIFY, so
   the SENDER frame should only settle (insert outputs, pay out), not re-run
   `verifySpend` (~243k wasted) or re-consume via the pool's own NonceManager.
   Post-approval failures must be impossible or prefix-checked: tree capacity,
   payout success, duplicate-output behavior, and settlement calldata all need
   explicit handling. No collision exists today (the pool's manager is a
   separate store from the protocol NONCE_MANAGER predeploy), but the
   redundancy should go.
9. **Retire the pool's own NonceManager/RecentRoots** once the bindings read
   protocol state, for minimality.

**Submission channel (interim):** builder-direct, to run the faithful spend
before items 1-3 land. No consensus change is needed; the spend is already
valid. The devnet exposes no public builder-submission endpoint, so an
end-to-end run today means a local node acting as the builder.

Everything pool-side through item 5's dependencies is deployed and waiting: the
proof check, the paymaster, and the spent-set binding (`NONCEKEYLOAD`).

## Source check (2026-07-11): the recent-root path is complete, and inclusion re-validates everything

Instead of asking ethrex the two questions the roadmap left open, we read the
`hegota-devnet` source at tip `2d64fba`. Both answers are yes, and the branch
carries documentation this review did not know about: `docs/eip-8141.md`,
`docs/eip-8250.md`, and `docs/eip-8272.md` specify the shipped opcode ABIs and
enumerate every divergence from the draft specs. The ethrex ask list shrinks
to one item.

**The recent-root path works end to end.** `RECENTROOTREFLOAD` is opcode
**`0xB5`**, not a `NONCEKEYLOAD` mirror as the 2026-07-06 commit note
suggested and not the draft's `0xB4` (which collides with EIP-8141's shipped
`SIGPARAM = 0xB4`). Stack `[field, index]` with `field` on top: 0 =>
source_id, 1 => slot, 2 => root; gas 3; envelope-only; legal in VERIFY
frames; exceptional-halt on out-of-range. Declared references are validated
twice: at mempool admission (slot window plus a head-state storage assertion,
because the validation-prefix simulation never reaches the VM's check) and
authoritatively in `execute_frame_tx` before any frame runs, where an invalid
reference invalidates the transaction and, on import, the whole block.
`entry_hash` commits to the raw slot, so a ring entry overwritten by an
aliasing newer slot can never satisfy a stale reference; per the ethrex doc
this closed the soundness hole that had kept references disabled, and the
path was deployed and proven e2e on the devnet on 2026-07-08. The native
write (64-byte `salt ‖ root` to the predeploy) commits under
`source_id = keccak256(pad32(caller) ‖ salt)`. That padded form diverges
from this repo's `RecentRoots.sol` emulation, whose `sourceIdOf` hashes the
unpadded 20-byte address; the migration must adopt ethrex's derivation, and
the draft EIP must ratify one form (ethrex flags it as needing upstream
ratification, since clients that disagree fork on the first
reference-carrying transaction). Upstream items for the draft are filed in
the vault at
`drafts/recent-root-references/reviews/2026-07-11-ethrex-implementation-divergences-claude.md`.

**Inclusion re-validates the whole prefix.** `execute_frame_tx`, the path
block import takes for type-0x06 transactions, re-checks against transaction
pre-state before any frame executes: static constraints, keyed-nonce
freshness (every selected key's current sequence must equal `nonce_seq`),
fee rules, every outer signature, and recent-root validity. Payer solvency
is enforced structurally: APPROVE debits the payer the transaction's maximum
cost, an underflow reverts the frame leaving `payer` unset, and an unset
payer invalidates the whole transaction post-execution. So the three
admission-to-inclusion races (another transaction consumed the nullifier,
the root aged out, the payer was drained) all invalidate the transaction,
and its block, at inclusion. Roadmap item 9's consensus precondition is
satisfied; what remains is mempool hygiene (admitted reference-carrying
transactions are not re-checked or evicted on head changes, only dropped
when block building fails them — latency, not soundness).

**The one missing capability is a payer TXPARAM.** The selector list runs
0x00–0x10 and nothing exposes the resolved payer, so a pool cannot route the
fee to whoever actually funded the transaction; the fee stays pinned to the
one known paymaster. This is the single remaining ethrex feature ask
(roadmap fee routing, and any relayer beyond the pinned one).

## Migration step 1 (2026-07-11): the root binding is protocol state, belt kept

Roadmap item 6 is implemented, both belts on. Four changes, validated by the
full deterministic smoke (real Groth16 proofs on-chain) and 51 forge tests;
the live Hegotá run is the remaining validation.

1. **`RecentRoots.sourceIdOf` adopts ethrex's padded derivation**,
   `keccak256(pad32(source) || salt)` (`abi.encode` instead of
   `abi.encodePacked`), so the pool's `sourceId` — and therefore its `domain`
   public signal — is the same value ethrex's native write commits under. A
   cross-stack vector test pins the form. This changed the testbed domain, so
   the fixture, and all its proofs, were regenerated (`gen_smoke.py`, new
   `TEST_SOURCE_ID`).
2. **`_publishRoot` dual-writes**: the RecentRoots emulation (still what
   `verifySpend` checks) plus the same 64-byte `SALT || root` payload to the
   native `0x…8272` predeploy, which on ethrex commits the entry at the
   current consensus slot. Forge tests assert the exact payload
   (`vm.expectCall`) and that a reverting predeploy fails the operation
   (`RootPublishFailed`) instead of letting the two stores diverge.
3. **The paymaster gains the root binding (1b)**: exactly one declared
   reference (TXPARAM `0x0F`), `source_id == keccak256(pad32(pool) || 0)`
   (computed in-EVM — KECCAK256 is not observer-banned — so no new
   constructor arg), and `ref.root == spend.root` via `RECENTROOTREFLOAD`
   (`0xB5`), disassembly-verified operand order. Runtime grew 468 → 509
   bytes; `run_live.sh`'s 2M deploy gas still clears it.
4. **`pool_frametx.py` declares the reference** on the faithful path:
   `recent_root_references = [[source_id, slot, root]]`, slot derived from
   the publication block's timestamp exactly as ethrex's `derivedSlotTime`
   does. The ref is RLP-embedded verbatim and covered by `sig_hash`
   (self-checked).

With this, a stale or forged root fails in the validation prefix under
protocol rules (committed entry + recency window, enforced at admission and
at block execution) rather than only in SENDER-frame execution.

**Validated live the same day** (public Hegotá devnet, chain 3151908, fresh
stack, throwaway operator, everything through the public
`eth_sendRawTransaction`, blocks 136448-136500):

- **Deploy**: `pool.sourceId()` on-chain equals the padded derivation
  computed independently in Python; paymaster runtime byte-matched.
- **Shield** (tx `0x27819c32…`, block 136463): the dual-write committed a
  native predeploy entry, read back via `eth_getStorageAt` and equal to
  `entry_hash(source_id, slot, root)` at the timestamp-derived slot 136479 —
  proving the script's slot derivation matches the node's `derivedSlotTime`
  (slot 136479 > block 136463: missed slots advance the counter).
- **Faithful transfer with a declared reference** (tx `0xa59b033f…`, block
  136473, 2,265,513 gas, pay frame 291,219 — the ref binding's gas cost is
  noise): payer = paymaster, both nullifiers at `NONCE_MANAGER` storage
  seq 1, root advanced to the fixture withdraw root. As far as we know the
  first reference-carrying frame transactions accepted on the devnet.
- **Negative controls** (simulator, withdraw spend): the same faithful shape
  with NO declared reference is rejected in the validation prefix
  (`valid=False, payer=None`), and — the sharp one — a reference carrying a
  COMMITTED, in-window root that is not the spend's root (the stale transfer
  root) is also rejected in the prefix. The protocol's committed-entry check
  passes for that reference; only the paymaster's `RECENTROOTREFLOAD` root
  equality kills it, so the B5 bind is load-bearing, not vacuous.
- **Faithful withdraw with a declared reference** (tx `0xc9795801…`, block
  136493): mined, nullifiers consumed, `withdrawalCredit[recipient]` = 0.55
  ETH.
- **Pushable claims closed the sponsorship loop live** (first time): a third
  party (the deployer key) pushed `claimFee(paymaster)` — the paymaster's
  balance rose by the two spends' 0.1 ETH fee credit — and
  `claimWithdrawal(recipient)` paid the recipient 0.55 ETH.

What remains for the fully clean design: step 2 (settle-only SENDER
entrypoint, retire the pool's NonceManager/RecentRoots — the reference path
is now live-proven, so step 2 is unblocked), and the payer TXPARAM for fee
routing beyond the pinned paymaster.

## Migration step 2 (2026-07-11): settle-only, protocol state is the sole authority

Roadmap items 8 and 9 are done: the pool keeps NO spent set and NO root
history. RecentRoots.sol, NonceManager.sol, and the Spend tuple's `slot`
field are deleted; `_publishRoot` writes only the native predeploy;
`sourceId` is self-computed (`keccak256(abi.encode(this, SALT))`). `_spend`
is settle-only: it binds the transaction envelope through
**EnvelopeProbe.yul**, a stateless Yul reader the pool
STATICCALLs because Solidity cannot emit the frame-tx opcodes. It requires
frames == 3, current index == 2, and the settle frame's target == the pool
(exactly-once per consumption: one frame is one call, so a settle frame
targeting the pool directly is the only entry; a re-entering intermediary,
even POOL_SENDER itself were it a contract, has a different frame target and
cannot double-credit one protocol nonce consumption, and the sender pin
already blocks any intermediary whose address is not POOL_SENDER),
nonce_keys == sorted{nf1, nf2} at seq 0, and one declared reference equal to
(sourceId, s.root); then it checks the proof and settles. The proof check
stays pool-side deliberately: the paymaster's prefix check protects its gas
float, the pool's protects noteholders, and the pool cannot authenticate WHO
verified in the prefix (a payer TXPARAM would allow exactly that — a second
use for the one remaining ethrex ask). Transfers and withdraws are therefore
faithful-shape only; the legacy self-paying path no longer settles. Tests
run against a mock probe armed with the envelope facts (the opcodes do not
exist on anvil); 41 forge tests and the real-proof smoke pass.

**Validated live the same day** (fresh stack, public mempool, blocks
136700-136720): shield `0x217ef8dc…` (block 136707), settle-only transfer
`0x6e7ff04e…` (block 136710, **1,939,893 gas vs 2,265,513 in step 1**, ~326k
saved by dropping the pool's own stores), withdraw `0x1be83aaa…` (block
136714). All four nullifiers sit at seq 1 in the protocol NONCE_MANAGER —
now the only spent set anywhere — the fee credits and the 0.55 ETH
withdrawal credit are correct, and the pool's only root store is the
EIP-8272 predeploy. Remaining known bounds: TreeFull is an accepted
operational limit (a revert there now burns notes for good; disposable
2^20-leaf testbed), and fee routing to arbitrary relayers still waits on the
payer TXPARAM.

## Security-review hardening (2026-07-11)

Three independent audits followed the migration: the Solidity, the two Yul
contracts (verbatim operand orders, offsets, and selectors checked against the
ethrex source, not the comments), and the Python tooling. No high or critical
defect survived; the load-bearing checks (nullifier/root binding, drain
closure, return-data handling, claim reentrancy) were confirmed sound. The
changes made:

- **Exactly-once no longer depends on POOL_SENDER's shape.** The one real
  finding: `frames == 3 && frameIndex == 2` proved a settle frame existed but
  not that the pool was entered once; a contract (or 7702-delegated) POOL_SENDER
  targeted by the settle frame could re-enter and double-credit one nonce
  consumption. `_spend` now also binds the settle frame's target == the pool
  (probe word 10, `FRAMEPARAM(2, 0x00)`), closing it independently of the
  paymaster and the sender's shape.
- **Fail-closed deployment.** The constructor rejects a zero sender and a
  code-less probe or verifier.
- **Bounded verify gas.** The pool's `verifier.verifyProof` and all three
  Groth16 precompile calls forward a fixed 500,000 (was 30,000,000); a valid
  verify is ~248k, and a corrupted-proof revert is now bounded. The probe
  STATICCALL cap rose 60k to 100k (its ~10 introspection opcodes are a fixed,
  non-inflatable cost) for opcode-repricing headroom.
- **Tooling and CI.** Yul deploy tools validate their mode/address arguments;
  `pool_frametx.py`'s recent-root reference is self-checked against the
  committed predeploy entry (a wrong derived slot fails loudly instead of
  broadcasting an unadmittable tx), and its now-single faithful path dropped
  the dead dual-path flags. Python deps are pinned; a CI workflow compiles the
  circuit, regenerates and diffs the Yul, runs the cross-language vectors and
  all 41 Forge tests, and gates `npm audit`. `SECURITY.md` records the trust
  boundary, the finite-tree burn condition, and the toolchain advisories.

Re-validated live on a fresh public-devnet stack (blocks 137366-137383): shield,
settle-only transfer (1,940,073 gas), and withdraw all mined through the public
mempool with the hardened 320-byte probe and the new settle-frame-target bind,
all four nullifiers consumed as protocol keyed nonces, credits correct.

## Tooling hardening pass (2026-07-11)

A follow-up review of the Python tooling found one high-severity operational
gap and fixed it plus three smaller ones. The on-chain core (circuit, pool,
paymaster, probe) was re-read in the same pass with no new findings.

- **Spends refuse to send without a simulation.** `pool_frametx.py`'s
  SENDER-revert guard silently disengaged when the endpoint lacked
  `ethrex_simulateFrameTransaction`: it printed a note and sent blind. For the
  settle-only pool that is the note-burn case, since a mined tx whose SENDER
  frame reverts has already consumed `{nf1, nf2}` as protocol keyed nonces at
  payment approval but never inserts the outputs. Nullifier-consuming spends
  now hard-refuse to send without a successful simulation (shields still
  degrade gracefully; a reverted shield loses nothing), and the post-mine
  revert message states explicitly that the spent notes are burned.
- **No gas down-sizing on spends.** The measured + 25% SENDER-frame resize
  traded paymaster prepayment for note loss: EIP-8037 state-dimension
  accounting varies 2-4x across blocks, so an OOG settle frame at a later
  block is the same irreversible burn. Spends keep the generous default limit;
  the payer's worst case is prepaying more gas, refunded on success. The
  resize still applies to non-spends.
- **The vector chain tests the v2 nullifier.** `export_vectors.js`,
  `PoseidonVectors.t.sol`, and `reference/poseidon_bn254.py` all still
  validated the legacy domainless `nf = Poseidon(TAG_NULL, spend_key, cm)`,
  a stale reference a future indexer could copy. All three now use the v2
  derivation `nf = Poseidon(TAG_NULL, Poseidon2(domain, spend_key), cm)`; the
  exporter draws `domain` after the existing LCG draws so every previously
  committed vector value is unchanged, and the vectors were regenerated
  (only `domain`, `nf`, `nf2` differ).
- **Doc drift.** The probe header now says ten returned words (was nine) and
  a 100k caller cap (was 60k, stale since the hardening pass raised it).

Acknowledged but deliberately unfixed: the paymaster does not compare the
proof-bound fee to the transaction's gas cost (harmless while the sender is
pinned to the operator's own key, required for a permissionless variant, and
recorded there alongside the SENDER-calldata bind); the pool has no
root-republish entrypoint (a quiet pool's predeploy entry ages out of the
8191-slot window until any shield refreshes it); and `gen_smoke.py --random`
persists no change-note secrets, so shielding real value through it strands
the change (fixture-driven testbed design). Validated: vectors re-exported,
`reference/poseidon_bn254.py` and `wallet.py` self-checks, `frametx.py`
golden vector, both Yul builds, and all 41 Forge tests.

## Gas review (2026-07-11): Poseidon codegen, and where the verify frame's floor is

A pass looking for gas reductions, with the VERIFY-frame side as the
priority, under the constraint that nothing about the statement, the
bindings, or the trust structure changes.

**The verify frame is precompile-floor-bound.** `verifyProofOnly` measures
246k, of which the snarkjs verifier is 241k: 181k is the one pairing check
(45k + 4 x 34k, EIP-1108 protocol pricing), 54k the nine public-input scalar
muls (9 x 6k ecMul), and ~6k the verifier's own field checks and dispatch.
Within this statement there are ~5k of total slack, so no implementation
change moves the number. The two real levers both change the statement or
the architecture, and are documented rather than taken:

The later native-ETH circuit keeps all nine public signals but exposes one
simple implementation exception to that historical conclusion: transfer fixes
both `publicAmount` and `ctx` to zero. An isolated verifier patch that skips
the identity `ECMUL(0)` and `ECADD` terms reduced the measured Yul transfer
proof from 247,159 to 234,350 gas, saving 12,809 gas without changing the
circuit or statement. Its generic zero branches add 273 gas to the all-nonzero
withdrawal path; separate entry points avoid that. On the next circuit
regeneration, packing the two 126-bit amount fields into one 252-bit public
field should save about another 6.3k on withdrawals. The
current live self-paying path is about 286.4k including the 40k first-use
charge for two keyed nonces, so a tightly tested 300k to 310k frame declaration
is a more relevant target than treating the devnet's 500k cap as required.

- **Compress the nine publics to one** via an in-circuit keccak of
  `[nf1, nf2, outCm1, outCm2, root, domain, publicAmount, fee, ctx]`. Drops
  eight scalar muls (241k to ~193k onchain; the onchain digest recompute is
  one keccak of 288 bytes, well under 1k gas) at the cost of roughly 150k
  extra R1CS constraints, proving time from ~600 ms to seconds, and a full
  circuit/ceremony/fixture regeneration. Even then the verify frame sits at
  ~2x the standard 100k `MAX_VERIFY_GAS`, so this buys a better devnet
  number, not admission under a mainnet-standard budget. (Compressing with
  Poseidon instead is in-circuit cheap but the onchain recompute, 4 x hash3,
  costs more than the muls it saves.)
- **Fold the deployment-constant `domain` into the verification key** (add
  `domain * IC6` into IC0 at generation). Saves one mul (6k) but changes the
  Spend tuple, both function selectors, and every hardcoded offset in the
  paymaster and tooling. Not worth it alone.

**The redundant SENDER-frame re-verification is removable without the payer
TXPARAM.** The step-2 rationale for keeping `_verifyProof` in `_spend` was
that the pool cannot authenticate WHO verified in the prefix. It can: bind,
through the probe, frame 1's target == a pool-pinned paymaster address plus
frame 1's mode == VERIFY and flags == payment-only (frames == 3 and the
settle-frame target are already bound). In that grammar frame 1 is the only
frame that can approve payment, a transaction whose payment is unapproved is
invalid at consensus, and the pinned paymaster approves only after
staticcalling `verifyProofOnly` on calldata it binds byte-for-byte to frame
2's Spend tuple. Inclusion therefore implies the proof was verified over
exactly this spend, and the in-execution re-verify (~246k, ~13% of the live
transaction) can go. Two costs keep this a decision rather than a change:
the pool and paymaster become mutually pinned (deployable with a CREATE
address precompute), and a paymaster bug becomes a noteholder bug, where
today the pool's own check protects noteholders independently of the
paymaster protecting its gas float. The payer TXPARAM would still be the
cleaner form of the same authentication.

**Implemented: the Poseidon codegen pass.** Tree hashing dominates the
execution frame (~22 hash2 per spend at ~35k each, ~65% of a settle), and
the generated Yul had real slack. Three output-identical changes to
`gen_poseidon_sol.py`:

- **Lazy reduction.** `mulmod`/`addmod` reduce arbitrary 256-bit operands,
  so intermediate reductions are unnecessary: the ark and mix now use plain
  `add`, with state bounded by (t+1) * P < 2^256 (holds for t <= 4 over
  BN254; the generator documents the bound), inputs reduced once at entry,
  and one final `mod` on the output.
- **Inlined s-box.** x^5 as straight-line mulmods instead of a Yul function
  call per lane per round, reusing `n0` (dead until the mix rewrites it) as
  the x^2 scratch because a fresh local is one stack slot too many for
  legacy codegen in hash3.
- **One loop kept.** A three-loop variant (full/partial/full, no per-round
  branch) measured slightly faster but duplicated the inlined mix matrices
  for +1.3KB of runtime, and the devnet prices code bytes at ~1,545 gas
  each at deploy; the single loop with an `if` for the full-round lanes
  keeps the library sizes unchanged (libsmall: T3 7,027 to 6,996 bytes, T4
  9,291 to 9,262).

Measured, default via-IR profile: hash2 34,957 to 29,326 (-16%), hash3
55,899 to 41,251 (-26%); under the legacy `libsmall` codegen the devnet
actually deploys: hash2 41,938 to 32,542 and hash3 58,413 to 45,284 (both
-22%). Operation level (via-IR): shield 894,845 to 761,946 (-15%), transfer
1,173,758 to 1,055,507 (-10%), withdraw 1,217,891 to 1,094,009 (-10%); the
live settle-only transfer (1.94M at the old codegen) should drop by roughly
200k, to be confirmed on the next live run. The hash function itself is
unchanged (same constants, same rounds, bit-identical outputs), so no
circuit, fixture, or vector changes: validated by the differential
circomlibjs vectors, the exhaustive incremental-tree test, and all 41 Forge
tests.

Also fixed in passing: `FOUNDRY_PROFILE=libsmall` no longer compiles the
test suite (the step-2 mock-probe tests are stack-too-deep under legacy
codegen, which had silently broken `run_live.sh`'s library deploys on a
fresh checkout); the profile now sets `skip = ["test/**"]`.

**Evaluated and rejected:** the circomlibjs optimized-schedule Poseidon
(sparse partial-round matrices) would have saved ~11k per hash2 before this
pass, but lazy reduction already captured most of that; the remaining ~3-4k
per hash does not justify ~6KB of extra constants, which only fit the
per-deploy budget as a separate data contract read back with EXTCODECOPY.
Dropping the `isLeaf` duplicate-commitment guard (two fresh SSTOREs, ~44k
per spend) would trade depositor protection for gas and stays.

## Cross-pool gas survey (2026-07-11): Tornado, Privacy Pools, Railgun

A source-level read of tornado-core, tornado-nova, tornado-trees,
0xbow's privacy-pools-core (zk-kit LeanIMT), Railgun-Privacy/contract, and
poseidon-solidity, looking for techniques this pool could adopt. Headline:
on everything those systems store onchain, this pool is already ahead,
because protocol state replaced the expensive parts. They pay a ~22k SSTORE
per nullifier (all three) where EIP-8250 keyed nonces cost the pool nothing;
they maintain root ring buffers with linear `isKnownRoot` scans (30, 100,
and 64 slots) where the EIP-8272 predeploy holds the window; Railgun loads
verifying keys from storage (~63k per verify) where the committed snarkjs
verifier keeps them in code; and Tornado classic's external MiMC costs
~50.9k per tree level against our 29k Poseidon. What remains is four real
techniques, none free:

- **LeanIMT (Privacy Pools, zk-kit): the biggest available lever, and a
  statement change.** A node with only a left child equals that child, so
  there is no zeros table and an insert hashes only where the index bit is
  1: popcount(index) hashes, average depth/2, with depth = ceil(log2(size))
  growing as the pool fills. At devnet-realistic sizes that is ~5 hashes per
  insert at 2^10 leaves and ~10 at 2^20, against our fixed 20-21, cutting
  settle-side tree hashing 50-75%. It composes with the EIP-8272 path (the
  root stays a bare 32 bytes; the salt could even encode tree size, which
  the LeanIMT root natively lacks), keeps arity separation (our tagged
  3-input leaves vs 2-input nodes), and with maxDepth 32 it retires the
  TreeFull burn condition. Costs: the circuit's Merkle template becomes
  zero-sentinel/muxed (same constraint count; 0xbow's extra `actualDepth`
  public signal is vestigial and we would not copy it), so circuit,
  ceremony, and fixtures all regenerate. The natural v3-statement item.
- **keccak-compressed binding publics (Nova's extDataHash, Railgun's
  boundParamsHash, 0xbow's context).** Fields the circuit never
  arithmetizes over are folded into one public signal checked onchain:
  `require(extDataHash == keccak256(abi.encode(fields)) % p)`, with only a
  dummy square constraint in-circuit. This sidesteps the reason the earlier
  Poseidon-compressed claim was dropped (the compressor runs onchain where
  keccak is near-free, not in-circuit). Honest arithmetic for OUR
  statement: nf1, nf2, outCm1, outCm2, root, and domain must stay direct
  (circuit outputs, membership, and nullifier derivation), so the fold
  reaches ctx and the publicAmount/fee pair (merged in-circuit into one
  conserved total, split onchain, Nova-style): 9 publics to 8, or 7 with
  domain folded into the verification key, saving 6-12k per verify. The
  stronger reason to do it is not gas: the extData struct would finally
  bind `feeRecipient` (and the withdraw recipient) in-proof, closing the
  documented "fee recipient is envelope-bound, not proof-bound" caveat.
  Same regeneration cost as any statement change; worth bundling with
  LeanIMT if v3 happens.
- **Paired leaf insertion (Nova `_insert(leaf1, leaf2)`, Railgun
  `insertLeaves`).** Nova hashes a spend's two outputs as one level-0
  sibling pair and walks up once: exactly `depth` hashes per spend, which
  for us means 20 instead of ~22 (~58k). But Nova gets the guarantee from
  a structural invariant, nextIndex is always even because every operation
  including deposits is a 2-output join-split. Our 1-leaf shield breaks
  that parity; without it the paired insert averages 21 hashes and is
  worse than the current shared-root-recompute at unlucky indices
  (Railgun's own batch algorithm measures k + depth - 1 average, its wins
  coming from cross-transaction batching, k = 2m outputs per block, which
  the single-operator devnet pool does not have). Verdict: adopt only if
  shield ever becomes a join-split; not worth reshaping the deposit path
  for ~58k.
- **poseidon-solidity (used by Privacy Pools): 21.1k per T3 hash, but
  23.5KB of runtime.** Its extra edge over our 29.3k comes from full
  unrolling with inline constants, which is exactly what the hegota deploy
  budget (~10.7KB) forbids; its T4 (14.2KB) does not fit either. Its
  remaining techniques (lazy reduction, x^5 via squared squares, scratch
  memory) are already in our generator as of this pass. The middle form,
  T4-style shared round functions with the round constants as call-site
  literal arguments, was BUILT AND MEASURED, and fails on this toolchain:
  under via-IR the inliner expands all 65 call sites regardless of
  optimizer runs (29.5KB, over EIP-170, and per-path compiler restrictions
  cannot isolate a library the test unit imports), and under the legacy
  runs=1 profile the devnet deploys, unoptimized call sites shuffle nine
  stack items per round and hash2 measured 65.9k, twice the loop form's
  32.5k. A second variant, keeping the loop but un-hoisting the modulus to
  inline PUSH32s, also measured worse under legacy (55.4k): at runs=1 the
  optimizer rematerializes repeated 32-byte literals through CODECOPY, the
  same heuristic foundry.toml already documents for via-IR at low runs.
  Via-IR gas is insensitive to the hoist either way (29.3k both forms).
  Conclusion: the committed loop form with the hoisted modulus is the
  local optimum for this solc under the deploy budget; the ~8k/hash to
  poseidon-solidity's number is the price of fitting in 10.7KB.

Also checked and consciously not adopted: Railgun's frontier
pre-initialization (it shifts ~17k per slot from first users to the
deployer but costs more at deploy than it ever returns; our constructor
already reasons this), Tornado's hardcoded zeros ladder and mappings over
arrays (we have the ladder; the mapping delta is tens of gas), Nova's
16-input consolidation circuit (a second vkey for a fragmentation problem
the testbed does not have), and tornado-trees' SNARK-verified 256-leaf
batch updates with a single sha256-bound public input (the endgame if tree
hashing ever dominates at scale, but it defers spendability behind a
roller and adds a second circuit).

## Bottom line

The devnet implements the three EIPs cleanly and the pool ran on it end to
end as a genuine frame-native application: shield, join-split transfer, and
withdraw all mined as type-0x06 frame transactions with the Groth16 proof
verified **onchain inside the SENDER frame, no attester**, the milestone the
whole prototype was aiming at. The 2026-07-06 devnet update closed the two
capability gaps this section used to name: `NONCEKEYLOAD` (0xB9) and
`RECENTROOTREFLOAD` now expose `nonce_keys[i]` and the recent-root references
in-EVM, so both trusted bindings can become proven, and the multi-VERIFY
`[only_verify, pay]` grammar the faithful shape needs is live. As of 2026-07-09
the faithful proof-in-VERIFY spend also mines through the public mempool (tx
`0xabcc9c82…`, block 104569): the 2026-07-08 devnet update cleared the verify
budget and non-zero keyed nonces, and two pool-side changes cleared the
observer without a VOPS allowance, a `verifyProofOnly` that reads no non-sender
storage plus a `GAS`-free validation trace (fixed-gas precompile and verifier
calls). No builder-direct path was needed. Pool-side, the last protocol binding
is `RECENTROOTREFLOAD`, followed by a safe settle-only SENDER entrypoint (which
also removes the redundant second `verifySpend`). The operational lessons that cost the
most time were self-inflicted or diagnosable: fund from the genesis set (not the
faucet), budget deploys at ~1,545 gas per runtime byte under the 2^24 per-tx
cap, fund the pay-frame paymaster (it is the payer), and treat a
perpetually-pending tx as a builder-side rejection with no error surface.

## Per-transaction paymaster selection (2026-07-11)

The pool does not pin frame 1 to one paymaster: settlement accepts a fee
recipient, and each proof paymaster binds that recipient to its own address.
The transaction builder now exposes `--paymaster 0x...`, overriding the
deployment default and using the same address for the VERIFY payment frame and
the settlement credit. The pool routes the address through `_feeRecipient`, a
backward-compatible seam for replacing the calldata value with an authenticated
resolved-payer TXPARAM when ethrex exposes it. A real-proof Forge lifecycle
selects two passive paymaster addresses for consecutive spends and confirms
their credits remain isolated and non-redirectable.

The Yul self-binding also ran live on a fresh public Hegotá deployment. The
shield mined at block 141775 (`0x67f7ab8a…`). Paymaster A
(`0x0165878a…`) then funded the transfer (`0xff47ade9…`, block 141783,
1,742,745 gas), and paymaster B (`0x2279b7a0…`) funded the withdrawal
(`0x846e106b…`, block 141789, 1,882,784 gas). Simulation and receipts resolved
the intended payer in each case. After both spends, the pool held exactly 0.05
ETH of fee credit for A and 0.05 ETH for B. Independent push claims
(`0x29654d8a…`, `0x66cd0682…`) transferred each credit only to its recorded
paymaster and cleared both mappings. The test used an isolated temporary copy,
so it did not replace the repository's recorded deployment or fixtures.

## Shared contract sender validated live (2026-07-11)

Ethrex's contract-sender path works for the pool without a client change.
`SharedPoolSender.yul` is a test-only, storage-free capability probe whose
address is the EIP-8250 namespace. In frame 0 it checks the three-frame grammar,
the configured pool target and transfer/withdraw selector, two keys at sequence
zero, one reference, one signature, and no blobs before calling
`APPROVE_EXECUTION`. The proof paymaster and settle-only pool retain the full
proof, key-set, reference and byte-exact calldata checks. The outer signature
is therefore only transaction authentication data required by today's
paymaster grammar; it does not control the shared sender.

A fresh deployment broke the circular constructor bind by precomputing the
pool's CREATE address. Shared sender `0x0DCd1Bf9…` was deployed first with the
future pool `0x9A676e78…` embedded in code, then that pool was created with the
shared sender as its immutable `POOL_SENDER`. A negative simulation changing
frame 2's target returned `valid=False`, `payer=None`, and `validation prefix
frame reverted`, showing frame 0 rejected before the paymaster could approve.
The honest transfer then mined through the public mempool as type 0x06
(`0x21c62a0c…`, block 141923, 1,743,376 gas). Its RPC envelope records
`sender = 0x0DCd1Bf9…` but the sole signature signer is the unrelated deployer
EOA `0xf39Fd6e…`; frame 0 used 583 gas, frame 1 resolved the proof paymaster
`0x0b306bf9…`, and settlement credited that payer exactly 0.05 ETH. This is
live evidence that contract execution authorization does not require an
operator-held sender key. It is NOT yet safe as a permissionless sender: because
this capability stage delegates proof/key/root validity to the selected
paymaster, a malicious self-funded payer could approve a transaction using a
revealed victim nullifier and deliberately burn it when pool settlement reverts.
The production successor must verify the proof, exact nonce-key set and recent
root itself before `APPROVE_EXECUTION`. The remaining ethrex ask is the
authenticated resolved-payer TXPARAM; the other pool-side hardening is the
proof-bound fee-versus-gas rule and, later, deciding whether to consolidate the
duplicate proof checks.

## Production shared sender and bounded paymaster implemented (2026-07-11)

The capability probe has a production successor in source. Frame 0 now copies
the complete static Spend tuple from frame 2 and calls the pool's storage-free
`verifyProofOnly` before approval. It also requires
`nonce_keys == sorted{nf1,nf2}` at sequence zero, binds the one recent-root
reference to the pool source and proven root, checks transfer/withdraw shape,
rejects zero or non-canonical settlement recipients, and pins the 300k sender,
100k paymaster, and 10M settlement gas limits. The last check closes a separate
open-submitter burn: copying a valid spend with too little frame-2 gas must fail
before approval rather than consume its nullifiers and OOG in settlement. Only
then does the sender call `APPROVE_EXECUTION`.

This makes the paymaster's Groth16 check redundant and, under the devnet's 500k
validation-prefix limit, impossible to retain alongside the sender check. The
paymaster therefore authenticates frame 0 as the immutable shared sender,
keeps its byte-exact frame-2 and self-recipient bindings, and replaces its proof
call with `fee >= TXPARAM(0x06)`. Selector 0x06 is ethrex's single maximum-cost
definition: the value `APPROVE_PAYMENT` debits and later refunds down to actual
cost. Sender and paymaster frame limits are 300k and 100k, leaving the pool's
settlement verification as the second independent proof check.

`feeRecipient` remains the one settlement field outside the proof. This is an
explicit open-payer auction for the interim ABI: another paymaster may copy a
pending valid Spend, replace only that recipient with itself, sign a new
envelope, and race it. The user's inputs, outputs, amount and fee are unchanged;
the winner funds the transaction and receives the fixed fee, while the loser
fails keyed-nonce validation rather than mining a revert. An authenticated
resolved-payer TXPARAM removes this calldata choice in the next migration.

The pre-gas-binding sender candidate deployed at `0x68B1D87F…`
(`0x66ad8cc0…`) with the precomputed pool `0x3Aa5ebB1…`, which then deployed
at `0x8e00fc4f…`. The workspace external-spend cap stopped the run before
source-id retrieval, proof generation, paymaster deployment and adversarial
simulation. The subsequent gas-limit bind changed the sender runtime, so the
current source supersedes that deployed candidate. Those contracts hold no test
notes and are not validation evidence. Required completion with a fresh pair:
an honest arbitrary-signer spend, bad proof and wrong-key cases through a
malicious self-funded payer with `payer=None`, replay rejection, and fee values
immediately below and at `TXPARAM(0x06)`.

## Production shared sender validated live (2026-07-12)

The full adversarial suite ran on a fresh Hegotá deployment of the final
gas-bound runtime, from a disposable deployer funded out of the well-known
genesis account. The circular bind deployed as designed: SharedPoolSender
`0x27cc5Cd9…` (deployer nonce 5) embeds the pool's precomputed CREATE address,
and the pool `0x7cFD0Ea4…` (nonce 6) landed on exactly that address with the
sender as its immutable `POOL_SENDER`. Both runtimes were verified byte-equal
to the checked-in generators. Two independent paymasters (`0x66e5efe3…`,
`0xc72a24f2…`, 0.15 ETH each) and a 13-byte always-approve malicious payer
(`0x7395a10b…`, runtime `3615600b5760015f5faa005b00`, 0.06 ETH) completed the
rig. One operational lesson: the 576-byte sender runtime needs ~1.1M deploy gas
under EIP-8037, so the first attempt at a 1M limit failed and burned a nonce;
budget 2M.

The honest path proved the headline property. A spend signed by a
freshly generated key holding zero wei simulated valid and mined:
shield `0x9948b72e…` (block 142347, 1 ETH), transfer `0x0af5a301…`
(block 142399, 1,744,804 gas) paid by paymaster A, withdraw `0xad0e83de…`
(block 142406, 1,884,797 gas) paid by independently selected paymaster B. The
mined envelopes record the contract as `sender` with a single signature from
the unfunded key. Frame budgets held live: the sender's full authorization
(grammar, NONCEKEYLOAD key set, reference, Groth16 staticcall) used 250,824
gas of its pinned 300k on the transfer and 250,857 on the withdraw; the
paymaster used 42,355 and 42,345 of its 100k. Each paymaster accrued exactly
0.05 ETH, push claims (`0xafa68683…`, `0x3d9627bc…`) paid each credit only to
its own paymaster, both mappings returned to zero, and the recipient held the
proven 0.55 ETH withdrawal credit.

Every adversarial case failed closed, before payment approval, with
`payer=None` and `validation prefix frame reverted`. Through the funded
always-approve payer: a proof with one flipped bit in pA; a nonce-key set
that is not the proof's two nullifiers; and a settle frame down-gassed to 5M
against the pinned 10M. Replaying the mined transfer's exact raw bytes was
rejected at admission with `Nonce mismatch: expected 1, got 0`, invalid rather
than mined-and-reverted, so replay cannot burn notes. The economic bind is
exact to the wei: with the proof-bound fee at 0.05 ETH, a max-fee choice
giving `TXPARAM(0x06)` = 50,000,000,000,721,585 wei was rejected by the
paymaster while 49,999,999,991,784,987 wei simulated valid, and the observed
accept/reject flips tracked `fee >= max_cost` through the RLP-length-induced
total-gas wobble (10,443,555 to 10,443,579 gas across nearby max-fee values).

With this, the operator-held sender key is removed from the trust structure
and demonstrated unnecessary live: execution authority is the proof itself,
payment is an open per-transaction paymaster market with an exact solvency
bound, and the only signature in the envelope is arbitrary transaction
authentication data. The remaining migration items are unchanged: the
authenticated resolved-payer TXPARAM ask to ethrex, retiring the calldata
`feeRecipient` through `_feeRecipient`, and the documented interim
fee-recipient auction.

### Suite vectors archived (2026-07-12)

A review pass noted the adversarial simulations above were documented but not
persisted as replayable inputs. `devnet/vectors/2026-07-12-shared-sender-suite/`
now archives the run byte-exact: the live deploy config, the deployment-bound
fixture, the flipped-bit proof fixture, the mined transfer's raw bytes, and
every command with its recorded output. The suite flags it used
(`--nonce-keys`, `--settle-gas`, `--save-raw`) moved from the run's scratch
copy into the checked-in `pool_frametx.py`, and the wrong-key and raw-replay
vectors were re-executed from the archive with identical results. The vector
README classifies replayability honestly: nullifier consumption freezes the
bad-proof, down-gas, and fee-boundary vectors as recorded history, which is
the design working as intended. Future live suites should write their vectors
directory in the same pass as the run itself.

## No nonce race between private spends on one shared sender (2026-07-12)

The shared sender exists so many users transact through one address without an
operator key. The open question that leaves is whether they then contend for
that address's nonce: if the sender carried a single sequential account nonce,
two users' spends would race for the next value and the loser would be
rejected, reintroducing the serialization the design set out to remove. They do
not, because a spend's nonce is not the account nonce. Each spend declares its
two nullifiers as its EIP-8250 keyed-nonce set at sequence zero, so two spends
with disjoint nullifiers occupy disjoint namespaces under the shared address
and are both admissible in any order. This run proves that end to end on the
live deployment.

Two 0.25 ETH notes A and C were shielded into the existing suite pool
`0x7cFD0Ea4…` (`0xc08d30c1…` block 149768 leaf 5; `0x39cc320f…` block 149769
leaf 6). The fixture generator seeds its Merkle tree from the pool's five prior
`LeafAppended` leaves and asserts the reconstructed root equals the on-chain
`currentRoot` before appending A and C, so the fixture root R
`0x28212f1c…` is the root the pool actually held after both shields; block
149769 published it to the EIP-8272 predeploy. Two independent transfers then
proved membership against that one R with disjoint nullifier sets.

Before mining, both simulated valid at the same head: transfer A
`valid=True payer=paymasterA`, transfer C `valid=True payer=paymasterB`, each
at `nonce_seq 0`, neither depending on the other having landed. Both then mined
from sender `0x27cc5Cd9…` at `nonce_seq 0`: A `0xef2c645b…` block 149804
(1,815,133 gas, paymaster A, nullifiers `0x22ee9ffc…`/`0x0710bb15…`, outputs at
leaves 7-8) and C `0xd08e00dd…` block 149807 (1,648,914 gas, independently
selected paymaster B, disjoint nullifiers `0x0bfff7df…`/`0x0c64520a…`, outputs
at leaves 9-10). Two transactions from one sender both mined at sequence zero,
which a shared account nonce cannot express: the second would need sequence one
and reject at zero. Replaying either mined raw now produces exactly that
sequential rejection, `Nonce mismatch: expected 1, got 0`, because the spend's
keys are consumed and its sequence has advanced to one, while the other spend's
namespace was never touched. Disjoint keyed nonces, not ordering, is what
separates them.

The run reused the suite paymasters and a zero-balance throwaway envelope
signer; the shields were funded from the well-known genesis test key. It added
three flags to `pool_frametx.py` (`--note` to shield from a fixture's `shields`
array, `--spend-key` to drive the fixture's second transfer, `--root-slot` to
bind both spends to the shared root's publication block) and taught
`wallet/gen_nonce_race.py` to reconstruct the live tree. Inputs and recorded
outputs are archived byte-exact in
`devnet/vectors/2026-07-12-nonce-race/`. The 0.475 ETH now in output notes at
leaves 7-10 is reconstructable from the generator's deterministic seed; 0.025
ETH went to the two paymasters as the proof-bound fees.

## Pool-as-sender: the pool is the transaction sender, same-block spends proven (2026-07-12)

The slides (ACDE #240) specify `FrameTx { sender = PrivacyPool }`: one
contract is both the frame-0 VERIFY identity and the settlement target. The
suite deployment instead used a separate `SharedPoolSender` as the sender
with the pool pinned behind it, a split that existed only because Solidity
cannot emit the frame-tx opcodes. This run closed that gap: `ShieldedPool.yul`
reimplements the pool as a single Yul object that reads the envelope opcodes,
verifies the proof, emits `APPROVE_EXECUTION`, and settles the spend as a
frame-2 self-call (`msg.sender == address(this)` replaces the `POOL_SENDER`
pin). Poseidon and the Groth16 verifier remain the deployed Solidity
contracts, called from Yul. This is a RESEARCH IMPLEMENTATION for the
disposable devnet: only the opcode-facing shell must be Yul, so the preferred
production architecture is a thin immutable Yul dispatcher at the pool
address delegating settlement to the reviewable Solidity implementation; the
monolith exists to demonstrate the design end to end (header of
ShieldedPool.yul states this).

Equivalence to `ShieldedPool.sol` is enforced two ways, with the Solidity
implementation as the spec. The ported battery (`YulShieldedPool.t.sol`, 41
tests) drives the Yul pool with the fixture's real proofs at the same CREATE
slot so the domain binds, asserting identical roots, named error selectors,
events and ordering. The differential harness (`YulPoolDifferential.t.sol`)
deploys both pools side by side and fuzzes identical raw calldata through
them, comparing success, returndata bytes, and tree state word-for-word;
that pass forced the Yul dispatcher to reproduce solc's decoder exactly
(per-selector minimum calldata lengths, dirty-address-word rejection instead
of masking, callvalue guards on non-payables, the 0..20 `filledSubtrees`
bounds with identical revert encoding). Hand-written Yul cost three real
bugs before any deployment, all the same class (a helper clobbering scratch
memory another expression still held): the immutable loader corrupting the
Poseidon call selector, a wrong delegatecall "fix" reverted after a direct
test, and `domainVal()` corrupted by `sourceId()` mid-buffer, which would
have broken every live spend and which the ported suite caught through its
fixture-domain fail-fast.

Deployment (fresh stack from genesis account #3; addresses and slots in
`devnet/vectors/2026-07-12-pool-as-sender/`) surfaced one tooling defect:
the deployed pool code failed the byte-equality check not because the deploy
was wrong but because the naive first-0xfe split used to derive the expected
runtime is unsound for this object; the optimizer hoists the constructor's
zeroRoot constant into a data segment appended after the runtime subobject
(and the init contains eleven incidental 0xfe bytes). The check now derives
expected code by deployment simulation (`cast call --create`), which
confirmed the on-chain runtime byte-exact; `yul_pool.py` documents why it
deliberately has no `--runtime` mode.

The live run then validated the whole design. Smoke: shield `0x4f604bf4…`
(block 151509), transfer `0x1dd7a3a6…` (151513, paymaster A), withdraw
`0xd4a6ff2d…` (151517, paymaster B), every spend carrying
`sender = the pool itself`, frame-0 verify+approve executing the pool's own
code at ~247k of the 300k budget; credits exact, claims paid, pool balance
ended at exactly the change note. The headline: two transfers proven against
one root with disjoint nullifier sets, both dry-run valid at head 151545,
submitted back-to-back through the public RPC, were BOTH pending
concurrently from the one pool sender and BOTH MINED IN BLOCK 151546
(txIndex 0 and 1, status 1). The prior nonce-race run had proven only
replay independence (its transfers were submitted sequentially and landed
in different blocks); this proves same-block builder handling. It also
answers a mempool-policy question empirically: EIP-8250 leaves EIP-8141's
one-pending-per-sender guidance untouched, but the public Hegotá endpoint
and its builder pipeline accepted concurrent disjoint-key transactions from
one sender. That is one node configuration's policy, not evidence about
propagation behavior across other ethrex configurations. Replaying either mined raw is rejected at admission with
`Nonce mismatch: expected 1, got 0`, the consumed lane advanced, the other
untouched. Raws archived byte-exact in the vector directory.

## Classification, and the redundant settlement verify (2026-07-12)

Post-milestone review verdict, recorded so future work targets the right
layer: protocol research and prototype validation are the strong parts
(real proofs, differential fuzzing, ABI and event parity, archived raws,
byte-exact deployment checks, live same-block evidence); the implementation
is experimental. The fund-holding pool is 600+ lines of handwritten Yul
whose development surfaced four genuine Yul-class defects, all fixed and
regression-covered, all of the same manual-memory class that Solidity makes
unrepresentable. Differential fuzzing is strong evidence of equivalence,
not proof. No independent audits, no formal verification, no long-horizon
stateful invariant runs. The next production step is the thin immutable
Yul dispatcher delegating settlement to Solidity, not more Yul polishing.

The clearest remaining inefficiency is the settlement-frame Groth16
re-verification inside spendCheck, ~247k of the ~1.4M settle frame (~14%
of a spend). The original rationale for keeping it (v2 REVIEW: the pool
cannot authenticate WHO verified in the validation prefix) was written for
the split design and dissolves under pool-as-sender. The candidate
argument: settlement requires caller() == address(this), which inside a
frame transaction means tx.sender == the pool; execution only runs after
an APPROVE_EXECUTION with scope 0x2, which the protocol handler accepts
only from a VERIFY frame targeting tx.sender; the only code at that
address is this immutable pool, whose only APPROVE_EXECUTION site is the
empty-calldata frame-0 path, which approves only after binding the full
grammar, the exact keyed-nonce set, the recent-root reference, and
staticcalling verifyProofOnly on the byte-exact frame-2 Spend read from
the same signed envelope. Settlement executes as that frame 2, so the
tuple it settles is the tuple whose proof was verified, and trusting
frame 0 adds no third party.

The focused gate passed on 2026-07-12 and the change was then taken and
measured live (below). The public Hegotá branch was pinned at ethrex
`2d64fba453caf3eaf91fec135490ccff0dbf2412`. Its runtime approval handler
checks `frame_target == tx.sender` before setting `sender_approved` for
both scope 0x2 and scope 0x3
([source](https://github.com/lambdaclass/ethrex/blob/2d64fba453caf3eaf91fec135490ccff0dbf2412/crates/vm/levm/src/opcode_handlers/frame_tx.rs#L121-L173)).
The opcode handler first requires the executing call frame's destination
to equal the resolved frame target, so a helper reached below the target
cannot issue the approval on its behalf
([source](https://github.com/lambdaclass/ethrex/blob/2d64fba453caf3eaf91fec135490ccff0dbf2412/crates/vm/levm/src/opcode_handlers/frame_tx.rs#L204-L249)).
Mempool prefix validation repeats the target-to-sender check for every
VERIFY role carrying the execution bit, with anti-regression tests for
both only-verify and self-verify shapes; both tests pass at the pinned
revision. During execution, a SENDER frame
is entered only after `sender_approved` and receives `tx.sender` as its
caller
([source](https://github.com/lambdaclass/ethrex/blob/2d64fba453caf3eaf91fec135490ccff0dbf2412/crates/vm/levm/src/vm.rs#L1636-L1652)).
This matches EIP-8141's explicit rule that APPROVE_EXECUTION is valid only
when the resolved target equals `tx.sender`.

The pool call graph supplies the second half. Its external calls target
only the two immutable Poseidon libraries, the immutable verifier, the
immutable envelope probe, the recent-root predeploy, arbitrary pull-credit
recipients, and one self-staticcall whose selector and calldata are fixed
to `verifyProofOnly`. A callback from any library, verifier, probe, or
payout recipient has that callee as `caller`, not the pool. The sole path
with `caller == pool` below frame 0 is the fixed self-staticcall, which is
static and cannot enter either settlement selector. No pool path performs
an arbitrary self-call, DELEGATECALL, CALLCODE, CREATE, or SELFDESTRUCT.
`test_payout_reentrancy_cannot_enter_settlement_as_pool` exercises the
only adversarial external-control edge: a fee recipient callbacks with
ABI-valid transfer and withdraw payloads while the pool is paying it, and
both stop at `NotPoolSender` while the payout succeeds.

The conclusion is narrow. For this immutable pool-as-sender runtime,
frame-0 proof verification authenticates the exact frame-2 Spend strongly
enough that the settlement Groth16 check is redundant. It would not carry
over automatically to an upgradeable dispatcher or mutable implementation
binding. The removal retains everything else: `spendCheck`'s caller, frame
index/count/target, keyed-nonce set and recent-root bindings (exactly-once
settlement and non-frame rejection, independent of proof validity), and
the canonicity/range checks, split into `validateSpend` so settlement
keeps them without the pairing call while `verifyProofOnly` keeps the full
check for frame 0 and the paymaster carrier. The proof-attack tests moved
to the authentication boundary (`*_rejected_at_authentication` against
`verifyProofOnly`), the differential cascade excludes proof validity by
design (Solidity still rechecks in settlement, the Yul architecture
verifies once in frame 0, whose opcodes Forge cannot run), and the
payout-reentrancy regression pins the caller() == pool argument.

Measured live on a fresh pool (`0x09bb66c0…`, Poseidon/verifier/probe
reused, deployed code byte-exact): transfer 1,494,000 gas against
1,735,435 on the double-verification pool (-241,435, -13.9%), withdraw
1,633,984 against 1,875,441 (-241,457, -12.9%), the settle frame dropping
1,402,141 to 1,160,688. The adversarial cases still fail closed with
`payer=None` in the validation prefix, now provably on frame 0 alone: a
flipped proof bit and a 5M down-gassed settle frame are both rejected
before payment approval. Same-block inclusion reconfirmed (two disjoint
race transfers both mined in block 152407) and both replays rejected at
admission with `Nonce mismatch: expected 1, got 0`. Inputs and mined raws
archived byte-exact in `devnet/vectors/2026-07-12-single-verification/`.

Also on the efficiency table: batched spends with batched Groth16
verification, batched leaf insertion, frame-calldata reuse instead of the
duplicated proof tuple in the pay frame, storage and event trims, LeanIMT
(already surveyed), and the authenticated payer TXPARAM that retires the
calldata fee-recipient machinery (already a documented seam).

## Dispatcher refactor: the delegatecall-in-VERIFY wall, and the seam that survives it (2026-07-12)

The roadmap's next production step was "thin immutable Yul dispatcher +
Solidity settlement," and it is now built and live-validated. The 5116-byte
monolith (ShieldedPool.yul) is split into a 1425-byte immutable dispatcher
(ShieldedPoolDispatcher.yul) at the pool address and a 7352-byte Solidity
implementation (ShieldedPoolLogic.sol) reached only by DELEGATECALL. Storage,
address, msg.sender, and msg.value are the pool's when the logic runs;
sourceId/domain became runtime functions (immutables would resolve to the
logic's own deploy address, wrong under delegatecall). The hand-audited
fund-holding surface drops from ~5KB of Yul to ~1.4KB, and the settlement
equivalence oracle collapses to one: the Solidity logic IS the settlement,
with DispatcherPool.t.sol (25 tests: ported battery, storage-layout contract,
delegatecall-context identity, payout reentrancy, shield-parity vs the
ShieldedPool.sol spec) as the check.

The instructive part is what the local suite could not catch. The FIRST
dispatcher delegated everything, including the frame-0 proof check: frame-0
self-staticcalled verifyProofOnly, which the proxy DELEGATECALLed to the logic
so it would run with address(this) == the pool and compute the right domain.
That deploy (0x110f9fd7) passed ethrex_simulateFrameTransaction (valid=True,
frame-0 251,729 gas, status success) but the builder never included a single
transfer: pending 50+ blocks while the chain advanced. ethrex's validation
observer bans DELEGATECALL inside a VERIFY frame (the delegate target's code
could vary between simulation and inclusion, breaking validation determinism);
the dry-run simulator does not enforce it, the builder does, and a validation
rejection is silently dropped rather than errored (the finding-4 pattern).

The fix respects the real seam: the VERIFY frame's work (parse envelope,
verify proof, approve) MUST run at the sender address in Yul under the
observer, so proof verification is inlined into the dispatcher as a direct
STATICCALL to the immutable Groth16 verifier (observer-legal, exactly what the
monolith and SharedPoolSender did live), with domain computed in Yul. The
dispatcher carries the verifier as a second immutable (tail = impl ||
verifier) and grew 889 -> 1425 bytes. Settlement stays delegated. The inline
dispatcher (0xe3275ba1, byte-exact vs cast call --create) then mined the full
smoke lifecycle with sender = the pool (shield 152950, transfer 152952 at
1,501,073 gas, withdraw 152955 at 1,641,117), ~7k gas (~0.45%) above the
monolith single-verification pool for the delegatecall hop; reproved same-block
inclusion (two disjoint-nullifier transfers both in block 152969); and rejected
both replays at admission. Archived in
devnet/vectors/2026-07-12-dispatcher/.

Two honest notes. The single-verification argument does NOT carry over for
free: it now rests on the DISPATCHER's frame-0 code and its verifier binding
being immutable (both baked in the code tail, never stored), not on "one
monolithic contract." That holds for this deployment and is stated in
ShieldedPoolLogic's _spend comment. And the live flipped-proof / down-gassed
adversarial cases were confounded (rejected on Nonce mismatch, because the
reused race nullifiers were already consumed, before frame-0's proof check
runs); frame-0's proof-rejection logic is covered by the forge suite and is
the same inline-staticcall mechanism the monolith proved live, but a clean
live proof-rejection on a fresh note (needing live-tree reconstruction) was
not run.

## Dispatcher fresh-note adversarial validation (2026-07-13)

The final dispatcher evidence gap is closed. A disposable dispatcher pool
(`0xa85233c6…`) received a new 0.2 ETH note in block 162088, producing a
transfer whose nullifiers had never been submitted. The unchanged transfer
simulated valid with the expected payer and a successful settlement. Flipping
one bit in `pA[0]` made frame-0 authorization revert with `payer=None`; setting
the settlement frame to 5M gas likewise failed the immutable 10M envelope bind
before payment approval. Re-simulating the valid baseline afterward still
passed, proving neither negative result was a replay or nonce-mismatch
confound. The exact fixture, config, and three signed raws are archived in
`devnet/vectors/2026-07-13-dispatcher-fresh-adversarial/`.

The repository closure pass also made the dispatcher milestone the public
default in `README.md` and `SECURITY.md`. CI now regenerates and diffs the
dispatcher artifact, compiles the current Python surface, formats the full
Solidity tree, and rejects Forge lint warnings. A clean build discovers and
passes all 117 Forge tests. The disposable run also caught a stale operational
limit: current `ShieldedPoolLogic` deployment exhausted the script's 6M gas
cap. The deployment succeeded at 12M, and `run_live_dispatcher.sh` now carries
that tested limit.

## Authenticated payer routing (2026-07-15)

ethrex now exposes the payer selected by a payment-scoped `APPROVE`
(`0x01` or `0x03`) as `TXPARAM(0x11)`, right-aligned in a 32-byte word.
The pool consumes that protocol-authenticated value directly. This supersedes
the earlier interim `feeRecipient` design described in the historical review
entries above.

`EnvelopeProbe.yul` returns eleven words (352 bytes), with the resolved payer
last. Both settlement implementations reject a zero payer or any word with
bits set above bit 159, then credit the proof-bound fee to that address. There
is no fee-recipient calldata field and therefore no caller-selected routing or
post-consumption recipient-mismatch branch.

The settlement ABI is now:

- `transfer(Spend)`: selector `0xb9947fa0`, 548 bytes including selector;
- `withdraw(Spend,address)`: selector `0xd677b46e`, 580 bytes including
  selector.

The immutable dispatcher, monolithic pool, shared sender, paymaster, frame
builder, generated initcode, Solidity interfaces, and differential tests were
updated together. The paymaster still binds the complete proof-bearing
`Spend` tuple and the exact frame grammar; settlement independently reads the
same payer selected by payment approval. Existing deployments are ABI- and
probe-incompatible and must be redeployed against an ethrex build that
implements `TXPARAM(0x11)`.

Local verification passes all 123 Forge tests, including zero and
non-canonical payer words against the Solidity reference, monolithic Yul pool,
and dispatcher architecture. Python compilation, frame vectors, wallet/tree
fixtures, Poseidon vectors, Yul compilation, formatter, and warning-denying
lint gates also pass. At that point a fresh live deployment of the revised ABI
was the remaining integration check; the completed run is recorded below.

## Authenticated payer live closure (2026-07-15)

That integration check is now complete on a fresh deployment. Pool
`0x1275d096…` shielded 0.10 ETH in block 189110. An unrelated zero-balance
outer signer then submitted a proof-authorized transfer through paymaster A
in block 189135 and a withdrawal through independent paymaster B in block
189151. Both were admitted through the public RPC as type-0x06 transactions;
simulation and execution resolved the intended paymaster as payer, and
settlement credited each authenticated payer exactly the proof-bound 0.04 ETH
fee through `TXPARAM(0x11)`. The outer signer remained at zero balance.

Before consuming the transfer nullifiers, under-fee, corrupted-proof,
wrong-key-set, and down-gassed controls all failed in the validation prefix
with `payer=None`; the valid baseline still passed immediately afterward.
After the two spends the pool was exactly solvent for 0.08 ETH of fee credits
plus 0.02 ETH of withdrawal credit. Permissionless claims paid both
paymasters and the bound recipient, leaving every credit and the pool balance
at zero. Replaying either exact mined transaction was rejected at admission
with keyed-nonce `expected 1, got 0`.

Addresses, proof fixture, root-publication blocks, signed mined transactions,
negative vectors, transaction hashes, gas measurements, and final balances
are archived in `devnet/vectors/2026-07-15-trustless-loop/`. This closes the
trustless pool-side loop on the upgraded ethrex devnet. It proves normal
public-mempool admission, not carriage by a FOCIL inclusion list; actual
inclusion-list enforcement and omission validation remain an ethrex/consensus
milestone.

## Architecture correction: self-paying core, optional paymaster (2026-07-15)

The privacy protocol does not need paymaster functionality. The pool can be
both sender and payer. Its frame-0 VERIFY checks the proof, keyed nonce set,
recent-root reference, settlement data, gas limits, and proof-bound fee, then
uses combined execution-and-payment approval. The final SENDER frame settles.
Payment approval consumes the keyed nullifiers exactly as it does in the
sponsored shape.

The builder now emits this two-frame shape by default. The pool keeps the fee
instead of recording a fee credit owed to itself. `--sponsored` and
`--paymaster` retain the three-frame external-payer path as optional fee
abstraction. Settlement and `EnvelopeProbe.yul` accept and authenticate both
exact shapes.

All 126 Forge tests pass, including a two-frame self-paying settlement test for
the standalone Solidity reference, monolithic Yul pool, and thin dispatcher.
The generated Yul artifacts and Python builder were regenerated and checked.
The archived live runs above remain valid evidence for the optional sponsored
shape. They are not evidence that a paymaster is required. The fresh live
self-paying result is recorded below.

## Two-frame self-paying live closure (2026-07-15)

The default path has now completed a fresh end-to-end run on the public
Hegotá RPC with no paymaster deployed. The probe, logic, and dispatcher were
newly deployed as pool `0x0d4ff719…`; the immutable verifier and Poseidon
libraries were reused from the earlier same-day proof lifecycle to conserve
the disposable devnet account's ETH. A 0.10 ETH shield mined in block 191883.

An unrelated outer signer with zero ETH then submitted both private spends.
The transfer mined in block 191889 at 1,385,529 gas and the withdrawal mined
in block 191899 at 1,525,556 gas. Simulation resolved the pool itself as payer
for both. Decoding each signed type-0x06 transaction showed exactly two frames:
one VERIFY frame targeting the pool with combined execution-and-payment flags,
then one SENDER frame targeting the pool. There was no paymaster frame.

After the transfer, the three-leaf tree root matched the withdrawal proof's
root byte for byte. After both spends, `feeCredit(pool)` was still zero and
the bound recipient held exactly 0.02 ETH of withdrawal credit. The pool's
two gas debits totaled 2,911,085,020,377,595 wei. An ordinary claim paid the
recipient exactly 0.02 ETH, cleared the credit, and left the pool at the exact
accounting result of 77,088,914,979,622,405 wei. The outer signer remained at
zero throughout.

Replaying either exact mined transaction was rejected at admission with
keyed-nonce `expected 1, got 0`. Deployment details, proof fixture, signed
transactions, hashes, gas measurements, and accounting are archived in
`devnet/vectors/2026-07-15-self-paying/`. This proves the self-paying path on
one ethrex configuration, not FOCIL inclusion-list carriage.

## Ethrex payer versus paymaster audit (2026-07-15)

The live result confirms that ethrex's execution core supports the intended
split: combined approval makes the pool both sender and payer, while a
payment-only VERIFY target can be a distinct external payer. Most earlier
feature work was not paymaster-only. The higher validation budget,
validation-prefix simulation, keyed-nonce access, recent-root access, payer
bookkeeping, refund, receipt, and payer introspection also serve the
self-paying privacy path. Only the separate `pay` prefix and paymaster-specific
mempool policy are exclusive to external sponsorship. ERC-20 reimbursement
itself remains contract logic and is not implemented by the client or pool.

The public ethrex `hegota-devnet` branch at `2d64fba` still needs two cleanups.
First, it classifies every resolved payer, including `payer == sender`, as an
`accessed_paymaster`, so the non-canonical-paymaster pending limit can apply to
a self-paying pool. Balance reservation should remain general payer accounting,
but canonicality and paymaster-specific limits should be conditional on a
distinct payer or an actual `pay` frame. Second, its payment-only APPROVE path
does not require prior execution approval, while the current EIP-8141 text
does. The normal `only_verify + pay` prefix supplies that ordering, but the VM
must enforce it too.

The running devnet adds resolved payer selector `TXPARAM(0x11)`, but that patch
was not present in the public branch at `2d64fba`, whose selector table stops
at `0x10`. Both live shapes prove the runtime behavior: it returns the pool for
combined self-payment and the pay-frame target for sponsorship. Resolved the
next day: see the 2026-07-16 section below.

## Live revalidation and payer-TXPARAM reconciliation (2026-07-16)

A full independent re-run confirmed the repository state end to end. Locally,
the generated Yul artifacts are current, all Python tooling compiles and its
self-tests pass, formatting and warning-denying lint are clean, and all 126
Forge tests pass. A focused re-review of the fee-routing change confirmed the
resolved-payer word is validated identically in all three settlements (zero and
dirty-high-bits words rejected), the fee's only sinks are pool balance for
`payer == pool` and `feeCredit[payer]` otherwise, and the
`fee >= TXPARAM(0x06)` guard sits before combined approval in both frame-0
implementations (`ShieldedPool.yul` and `ShieldedPoolDispatcher.yul:285`). The
one named trust assumption is protocol-side: settlement believes ethrex sets
`TXPARAM(0x11)` to exactly the account the payment approval debited.

The live run deployed a fresh probe, logic, and dispatcher as pool
`0xf6f36f01…` (verifier and Poseidon libraries reused; the verifier's onchain
runtime is byte-equal to the current build) from a disposable key funded by a
genesis account. The default two-frame self-paying lifecycle then reproduced
the July 15 result on the public RPC: shield `0x2e362a4a…` (block 203728,
1,083,142 gas, identical to July 15), transfer `0xc6afbe6a…` (block 203732,
1,385,462 gas), withdraw `0x927f8147…` (block 203735, 1,525,580 gas), both
spends with exactly two frames, `sender == payer == pool`, submitted by a
zero-balance outer signer. Spend gas differs from July 15 by under 70 gas
(state-dimension variance). Accounting was exact: 0.55 ETH withdrawal credit,
`feeCredit(pool) == 0`, final pool balance 997,088,957,979,622,706 wei, which
is the 1 ETH shield minus exactly 2,911,042 gas of self-paid debits at the
effective price. An ordinary claim (block 203747, 35,538 gas) paid the
recipient and cleared the credit. Re-publishing either exact mined transaction
was rejected at admission with keyed-nonce `expected 1, got 0`.

The adversarial negatives ran against a second fresh dispatcher
(`0x9cac467d…`, sharing the immutable logic and verifier) with a
randomized-secret fixture: a flipped proof bit and a 5,000,000-gas down-gassed
settlement frame each failed in the validation prefix (`validation prefix
frame reverted`, no payer resolved, nothing consumed), and the same note then
transferred validly (`0x84d2ed43…`, block 204246). Two tooling findings from
the run: generating a second deterministic fixture against an already-used
deployment reuses the fixed-seed dummy note, whose nullifier is an
already-consumed keyed nonce (use `--random`, or a fresh deployment); and the
recent-root self-check's diagnostic blamed slot derivation for what is usually
a root mismatch from a non-empty tree, now fixed to name both causes.

Source reconciliation: the public `hegota-devnet` tip is `881f48d`
(2026-07-15), and commit `f5bfde5dc` ("Add a knob-gated resolved-payer
frame-tx TXPARAM index (0x11)", 2026-07-14) publishes the payer selector with
tests. The final ethrex feature ask from the July 11 source check is therefore
closed in public source. The VOPS validation-reads allowance remains open: the
public history shows `92954f85` ("Allow VOPS validation reads in VERIFY
frames") reverted by `2d64fba`, so the pool's storage-free `verifyProofOnly`
workaround remains load-bearing.

Two follow-ups from the same day. First, the recent-root expiry was hit live:
attempting the second pool's withdrawal after its reference aged past the
8191-slot window was rejected at admission (`slot 204265 is outside the usable
window at slot 213808`), and because the pool publishes roots only on insert
and the randomized fixture keeps no change-note secrets, that note is now
unspendable. This is the missing root-republish entrypoint demonstrated on
chain, not just predicted. A follow-up shield of an already-inserted
commitment was also correctly refused, by the contract's DuplicateCommitment
guard in simulation, before broadcast. Second, Dora frame support exists but
is not yet serving: `lambdaclass/dora` branch `frame-tx-view` renders type-0x06
transactions on the transaction and slot pages (`558bed7d1`, over a
go-ethereum frame-tx fork so the blocks decode), and its GHCR image published
2026-07-16 19:19 UTC, but `dora.hegota.ethrex.xyz` still runs the pre-frames
June 27 build (`git-0b10c78`): no frame type in its filter, the July frame
transactions absent at already-indexed heights, and a freshly mined type-0x06
shield (`0x8c6d1f33…`, block 213800) not found. Once the instance is switched,
the historical frame transactions will still need a re-index to appear. The
fork also carries an `eip7805-support` branch worth watching for the FOCIL
milestone.

## Root republish entrypoint (2026-07-16)

The quiet-pool deadlock demonstrated in the morning's live run is closed. All
three settlement implementations gained `republishRoot()`, a permissionless
entrypoint that re-stamps the existing current root at the present consensus
slot and emits `RootRepublished(bytes32 indexed root, uint64 slot)`. The
caller cannot inject a root, only refresh the true one, and an overwritten
ring position has already expired, so the function needs no authorization.
The reference implementation and the dispatcher logic add the one-line
external function; the monolithic Yul adds the `0x810a208e` selector case and
event emitter; the dispatcher itself needs nothing because it delegates all
nonempty calldata to the logic. Five new Forge tests cover the re-stamp
without insert, event and predeploy-payload exactness, any-caller access, the
reverting-predeploy path, and the delegatecall context through the proxy; the
suite is now 131 tests, all passing, with `forge fmt` and warning-denying lint
clean and the regenerated monolith artifact checked in.

`pool_frametx.py` now also fails fast when a reference's publication slot has
left the 8191-slot window, naming the remedy, instead of letting admission
reject the transaction at broadcast. The EIP-8272 draft gained a Security
Considerations paragraph: sources that write roots only on state change
SHOULD expose a permissionless refresh, or their consumers inherit a liveness
dependency on unrelated writes.

Validated live the same day on a fresh deployment (pool `0x3490ce6f…`, logic
`0xD45e547D…`): shield in block 217910, `republishRoot()` from an ordinary key
in block 217913 (52,680 gas, correct event, unchanged root), and the two-frame
self-paying transfer mined in block 217917 with its declared reference naming
the republished slot 217913 rather than any insert's publication. A quiet pool
is revived by anyone re-stamping its root, and a proof built against that root
spends without regeneration. Vectors in `devnet/vectors/2026-07-16-republish/`.
This supersedes the "no root-republish entrypoint" acknowledgement in the
tooling hardening pass above.

## Dora frames verified on Hegotá (2026-07-16, later the same day)

The operators switched `dora.hegota.ethrex.xyz` to a frames build
(`git-837c7cea`, not yet on the public branch) and frame rendering now works
for every transaction shape this project produced. The July 9 three-frame
faithful spend (`0xabcc9c82…`) renders with the two nullifiers as its keyed
nonce set and the `only_verify`, `pay`, `SENDER` frame table. Today's
self-paying transfer (`0xe60fb3f6…`) renders `From = the pool`, combined
`VERIFY APPROVE execution+payment` then `SENDER` both targeting the pool, the
keyed nonce set, and a dedicated recent-roots line showing the republished
entry (slot 217934, root `0x01b6868a…`, the pool's source id), which makes the
EIP-8272 reference a first-class explorer object.

Five rendering gaps to report upstream, each re-verified after the deploy
settled. Frame transactions render without their block and receipt join:
block 0, epoch timestamp, gas used 0, and a red failure badge on transactions
that succeeded. Every frame transaction shows a Created Contract line because
the missing top-level `to` is treated as contract creation; the displayed
address is exactly CREATE(from, 0), verified by recomputation, and the same
misread appears as "Contract Creation" in the slot page's transaction listing.
The overview value goes through a float64 wei-to-ETH conversion: both 0.1 ETH
shields render as 0.100000000000000006 (the float64 representation of 0.1 at
18 decimals) while the 1 ETH shield renders exactly, because 1.0 is exactly
representable. The details Nonce field shows 0 regardless of the transaction's
nonce_seq (a seq 9 shield shows Nonce 0 while the keyed-nonce line correctly
says seq 9), so it is not wired to the type-6 payload. And the build predates
the execution-transactions listing page, so `/transactions` is 404 and
discovery is by hash only. On the positive side, the post-upgrade instance has
healthy consensus indexing (slots proposed with proposers), and slot pages
render frame transactions with an 8141 type badge and the method decoded from
the SENDER frame's calldata.

## Proof in the VERIFY frame, publics-only settlement (2026-07-17)

The Groth16 proof no longer rides in the settlement tuple. The frame-0 VERIFY
frame carries the 256-byte proof as its calldata, and the SENDER frame settles
a publics-only 288-byte `Spend`. Settlement inputs are now exactly the state
transition's public signals, so the proof is a detachable validation-surface
unit: it composes with the EIP-8288 dependency-frame aggregation path and the
EIP-8141 signatures-list carrier (both read proofs outside the signed
settlement calldata) without a further ABI change. There is no statement or
circuit change: same nine publics, same verification key, same proofs.

Mechanics across the three implementations. The `Spend` struct drops
`pA/pB/pC`; `transfer`/`withdraw` become `0x4ebb583c` / `0x5f0c5052` and
`verifyProofOnly(Spend,uint256[2],uint256[2][2],uint256[2])` is `0x76314392`
(byte-identical calldata to the old tuple: publics then proof). The dispatcher
and monolith frame-0 code bind `frameParam(0, dataLen) == 256` and read the
proof from their own calldata, verifying it against the settle frame's publics.
The empty-calldata frame discriminator becomes `calldatasize() == 256` (no ABI
call is 256 bytes, and the envelope opcodes exceptional-halt outside a frame
transaction, so a stray 256-byte call reverts). `EnvelopeProbe.yul` gained a
nonzero-calldata proof passthrough returning `[frame0 dataLen, 8 proof words]`;
the standalone `ShieldedPool.sol` reference reads it to re-verify in settlement
(it alone keeps the belt-and-suspenders re-check), while the dispatcher logic
and monolith verify only in frame 0. The bounded paymaster binds the pay
frame's publics byte-equal to the settle frame and its proof bytes byte-equal
to frame 0.

Validated: all 132 Forge tests pass (the differential corpus and cascade were
updated for the new selectors and the reference's proof-passthrough arming),
fmt and lint clean, artifacts regenerated. Live on a fresh split deployment
(pool `0xdd93876a…`, logic `0x6a97CC25…`, probe `0xe6ecce2c…`): the two-frame
self-paying transfer (block 225026) and withdrawal (block 225030) mined with
frame 0 carrying 256 bytes and the SENDER frame 292, confirmed in Dora's frame
view; gas within ~800 of the pre-split run; replay rejected at admission. On a
fresh empty pool, a proof flipped in frame 0 and a down-gassed settle frame
both failed in the validation prefix, and the unmodified spend then mined
(block 225052). Vectors in `devnet/vectors/2026-07-17-proof-in-verify/`.

One productionization note surfaced: `ShieldedPoolLogic` now deploys at
~11.7M gas under EIP-8037 state-dimension accounting, close to the EIP-7825
per-transaction cap of 2^24 = 16,777,216. A LeanIMT or logic split would give
headroom; today it deploys but the margin is worth tracking.

Dora update: `dora.hegota.ethrex.xyz` is now serving the frame-tx build. It
renders the full frame view (per-frame mode/scope/target/data-length/gas,
keyed nonces, recent-root reference, resolved payer) and several earlier
rendering gaps are fixed in this build: the block/receipt join is correct
(real block number, Success status, real gas), and frame transactions no
longer show a bogus Created Contract line.

## Dora rendering fixes re-verified on a fresh lifecycle (2026-07-17, evening)

The operators shipped a further Dora build (`git-1c3f6e23`, replacing
`git-837c7cea`) claiming fixes for all five gaps filed on 2026-07-16, with the
nonce fix visible only on newly indexed transactions. Verified against a fresh
split-architecture lifecycle on a new pool (`0xf02a4398…`, source
`0x704e6540…`): shield 0.1 ETH from an account at nonce 83 (block 228503),
self-paying transfer (block 228524), self-paying withdrawal (block 228847).
Vectors in `devnet/vectors/2026-07-17-dora-render-fixes/`.

Four of the five gaps are fixed, and one is partially fixed. The block/receipt
join is correct on new and pre-upgrade transactions alike (real block, real
timestamp, Success status, real gas; the misleading failure badge is gone).
The bogus Created Contract line is gone retroactively; the transaction page
now renders From and To from the frame structure instead of deriving
CREATE(from, 0). Values render exactly (the 0.1 ETH shield shows "0.1 ETH",
not the float64 artifact 0.100000000000000006). The details Nonce field is now
wired to the type-6 payload: the seq-83 shield shows Nonce 83, matching the
keyed-nonce line, and pool-sender spends correctly show 0. Search now resolves
frame transactions (`/search?q=<hash>` 301-redirects to the transaction page),
and the new execution indexer (`data-execution-indexer-enabled`) also lists
frame transactions on address pages, so discovery is no longer by hash only.

Three residual gaps, none blocking. There is still no global `/transactions`
listing page (404; `/txs`, `/el_transactions` likewise). Search resolves only
transactions the new execution indexer has seen: pre-upgrade hashes such as
the 2026-07-16 transfer `0xe60fb3f6…` return an empty result page, though
their `/tx/` pages render fully. And address pages index only the top-level
from/to, so a shield does not appear on the pool's address page even though
its SENDER frame pays the pool 0.1 ETH; frame-level targets are not indexed
per address.

Two operational notes from the run itself. `ShieldedPoolLogic` now measures
12,481,836 deploy gas under EIP-8037 (up from the ~11.7M noted yesterday); the
script's former 12M limit failed at the code deposit and
`run_live_dispatcher.sh` now passes 14M, leaving ~2.3M of headroom under the
EIP-7825 cap before the logic needs a split. And the fixture's 0.005 ETH
proof-bound fee no longer clears `fee >= max_cost` at the default ~1 gwei max
fee with the settle frame pinned at 10M gas (max_cost ≈ 0.0104 ETH): the spend
is rejected in the pool's own VERIFY during simulation. The run passed 0.4
gwei (`--max-fee-per-gas 400000000 --max-priority-fee-per-gas 400000000`,
max_cost ≈ 0.00415 ETH). Post-state exact: pool balance 0.098836… ETH is the
0.1 ETH shield minus precisely the two spends' gas at 0.4 gwei, with the
0.02 ETH withdrawal held as a recipient credit.

## Self-pay no longer requires the payer TXPARAM (2026-08-13)

The public Hegotá endpoint at ethrex revision `eef9f712` now accepts a prefix
budget of 500,000 gas and rejects 500,001. Its chain configuration still leaves
`payerTxparamTime` unset, so `TXPARAM(0x11)` exceptional-halts. The previous
envelope probe read it unconditionally. A proof-bearing transfer therefore
passed frame 0 at 286,186 gas, then reverted its SENDER frame after consuming
about 100,000 gas in the capped probe call.

The minimal correction is structural. In the exact two-frame grammar, the
pool's combined execution-and-payment approval makes the pool both sender and
payer, so settlement derives `payer = address(this)` and the probe leaves its
eleventh word zero. Only the optional three-frame sponsored grammar executes
`TXPARAM(0x11)` and validates the returned address. This change is applied to
the Solidity reference, monolithic Yul pool, and dispatcher settlement logic.
Their self-pay regressions now pass a zero payer word, while sponsored zero and
dirty-high-bit payer words still revert.

The patch was validated live before being filed. A fresh dispatcher at
`0x57436abb4005b24763ce8166b44b520ea1a89c7d` completed shield
`0xabb3c783f13547e681b6ee9c8fd0f2a13239aa82836bec2f0fc95ec2bcbbb19e`
(block 29,010), private transfer
`0x384cf35b934a1b0c44197b4b866f6c7e94efb421004beb46b1cfa4cbfd892e85`
(block 29,012), and private withdrawal
`0xaeee541e9535b317c51f716bb4f6ad51f27f1d090b3a37760a132f389ccda4f6`
(block 29,014), all with receipt status 1. The self-paying core is therefore
compatible with deployments where the payer selector is disabled. Sponsorship
still requires that selector to be active.
