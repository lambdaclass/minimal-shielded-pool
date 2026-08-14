---
type: research
tags: [privacy, focil, testing]
status: in-progress
updated: 2026-08-07
---

# Latest FrameTx and FOCIL conformance tests

> This page records the 2026-08-07 protocol and FOCIL campaign. Its pool
> transactions predate the hardened 2026-08-14 deployment. Use
> `vectors/2026-08-14-tight-gas-profile.md` for current pool evidence.

## TL;DR

The happy path, replay rejection, keyed-nonce concurrency and overlap, post-approval durability, recent-root boundaries, reorg eviction, Profile 2 classification, and proof-heavy public gossip are validated on ethrex `b23e206`. A live private spend referenced a root at exactly `S+1`, and two independent peers saw it pending before inclusion. Two gaps remain: builder-claimed indices are not wired, and the public devnet exposes no direct includer or authenticated Engine endpoint for the remaining end-to-end FOCIL tests.

## Specification pins

This plan was checked on 2026-08-07 against [EIP-8141](https://eips.ethereum.org/EIPS/eip-8141), [EIP-8250](https://eips.ethereum.org/EIPS/eip-8250), and [EIP-8272](https://eips.ethereum.org/EIPS/eip-8272), all Draft. EIP-8369 is the Informational draft in [ethereum/EIPs PR 12110](https://github.com/ethereum/EIPs/pull/12110), pinned here at `c52c9fdb1f9d24ef8b703b7fb3d28bde52a81937`. It does not activate consensus rules by itself. Its claimed-index mechanism still needs a Standards Track EIP-7805 extension.

Every run must record the exact ethrex and EIP revisions. A later EIP edit can change the expected result.

## Priority test matrix

| Priority | Test | Exact pass condition | Covers |
|---|---|---|---|
| P0 | Partial-overlap race: `{a,b}` versus `{b,c}`, same sender and `nonce_seq = 0` | Both are valid at the parent state, but a block cannot execute both. After one lands, the other is rejected because the shared key is at sequence 1. Pool state changes only for the included spend. | 8250 |
| P0 | Claimed-index conflict | If the omitted overlapping spend is evaluated before the conflicting spend, omission is unjustified. If evaluated after it, omission is justified. Missing, malformed, and out-of-range claims behave as end-of-payload claims. | 8369, 7805, 7928 |
| P0 | Post-approval settlement revert, using disposable notes | A successful payment-scoped `APPROVE` consumes both keys and identifies the payer. A later SENDER-frame revert leaves the keys consumed, appends no leaves, and makes retry fail. The block remains valid. | 8141, 8250 |
| P0 | Recent-root boundaries and reorg | A root written in slot `S` is rejected in `S`, accepted from `S+1` through `S+8191`, and rejected at `S+8192`. A transaction referencing a root removed by a reorg is evicted or rejected, and a block containing it is invalid. | 8272 |
| P0 | Profile 2 positive and negative classification | The current no-blob `self_verify` pool spend is enforceable below the `2**20` budget. A blob, extra VERIFY frame, validation read outside the allowed surface, or `2**20 + 1` budget is not enforceable; omitting those variants must not make the block noncanonical. | 8141, 8369 |
| P0 | Public gossip versus direct includer delivery | Test with two EL peers. Record whether the proof-heavy transaction propagates through the public pool, then submit the same shape directly to an includer. Direct delivery can be FOCIL-enforceable even when public propagation rejects it. RPC acceptance on one builder node is not proof of public gossip. | 8141, 8250, 8369 |
| P1 | IL budget and identity boundaries | Costs exactly at `2**20` pass; one gas over does not. Test two transactions whose sum is exactly the cap and one over, invalid-signature budget debit, duplicates across ILs, and byte-distinct equivalent envelopes. Every node computes the same admitted set. | 8369 |
| P1 | Composed envelope and signature vector | One vector fixes the final RLP order for 8141 + 8250 + 8272. Mutating any nonce key, sequence, recent-root tuple, frame, sender, or fee field changes the signing hash and invalidates the signature. Ethrex and an independent encoder agree byte for byte. | 8141, 8250, 8272, 8369 |
| P1 | First-use surcharge and receipts | Two fresh keys add exactly `2 * 20,000` gas inside the approving frame. One gas below the required approval budget fails atomically with no payer, nonce, or pool-state change. Receipt gas, refund, and payer balance delta reconcile exactly. | 8141, 8250 |
| P1 | Recent-root writer and reference edges | Only a direct zero-value 64-byte call writes. Wrong length, nonzero value, STATICCALL, DELEGATECALL, and CALLCODE fail. Last write in one slot wins. Sixteen references decode; seventeen fail; duplicates remain separately charged. | 8272 |
| P1 | BAL reconstruction and multi-node fork choice | Prior transactions change each dependency in turn: keyed nonce, recent root, payer balance, low storage slot, code, and delegation. Claimed-index replay matches full execution, and all CL/EL pairs agree on canonicality. | 7928, 8369, 7805 |

## Pool-specific guarantee boundary

The pool is also the payer. Test two spends that are both affordable at the parent state but where the first spend's gas debit makes the second unaffordable. EIP-8369 intentionally lets the builder claim the later index and justify omission unless payment capacity is reserved. This is an expected weaker guarantee for a shared payer, not an ethrex failure.

The earlier concurrent run through one public RPC proved that ethrex's local pool and builder accepted disjoint keyed transactions, but not peer propagation. The later slot-11,298 run closes that evidence gap for one proof-heavy spend: `rpc2` and `rpc3` both saw the transaction pending after submission to `rpc1`. It still does not establish every ethrex configuration or the separate direct-includer path. EIP-8250 removes the consensus dependency but retains EIP-8141's conservative one-pending-transaction-per-sender public-mempool guidance.

## 2026-08-07 run status

| Test | Status | Finding |
| --- | --- | --- |
| Partial overlap | Pass live | One valid spend mined; its `{b,c}` rival was rejected while `{a,b}` was pending, then failed on shared key `b` after inclusion. |
| Claimed-index conflict | Missing live wiring | The rival was valid before the conflict and invalid after it, and index fallback unit checks pass. No builder claim reaches the EL and replay still uses final block state. |
| Post-approval revert | Pass live with isolated probe | Payment approval consumed two keys; the later SENDER revert produced status `0`, did not undo the keys, and did not invalidate the canonical block. |
| Recent-root boundaries and reorg | Pass at EL rule and eviction level | A live spend mined at exactly `S+1`; exact-revision tests passed `S`, `S+1`, `S+8191`, `S+8192`, missing-entry rejection, and post-head-change eviction. A full CL-driven Engine reorg remains untested. |
| Profile 2 classification | Source-level gates pass; live Engine replay unavailable | The live no-blob `self_verify` spend is a 352,800-gas candidate. Exact-revision tests accepted the eligible shape and excused omission for blob, extra-VERIFY, over-budget, and out-of-surface cases. |
| Public gossip versus direct includer | Gossip pass, direct unavailable | A transaction sent to `rpc1` appeared pending on independent `rpc2` and `rpc3` before mining. Public endpoints expose no Engine or direct-includer method. |

Exact transactions and limitations are recorded in
[[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-07-overlap-index-revert/README]]
and
[[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-07-root-profile2-gossip/README]].

## Minimum next run

Implement and test the claimed-index data path next: commit the builder's index, default missing, malformed, and out-of-range values to the payload end, and replay against state immediately before that index. With operator access, finish the CL-driven reorg, omitted-payload, and direct-includer tests through the authenticated Engine API.

## See also

- [[prototypes/minimal-shielded-pool/index]]
- [[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-07-concurrent-private/README]]
- [[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-07-overlap-index-revert/README]]
- [[prototypes/minimal-shielded-pool/devnet/vectors/2026-08-07-root-profile2-gossip/README]]
- [[research/focil/FOCIL and Native AA]]
- [[drafts/focil-eligibility/eip-draft]]
