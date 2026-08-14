# Recent-root, Profile 2, and public-gossip evidence (2026-08-07)

## Target

The public Hegotá nodes reported chain ID `3151908` and
`ethrex/v23.0.0-HEAD-b23e2068475231f2f5c8dd7bf25d916370e9024e`.
The live test used pool `0x593da3f0c8587af52fab28008422d2f526703cdb` and
source ID `0xe21fc6e5b95c64f7533dca997009638aa03f7325a024597b62e9aada4aad880b`.
No private key or note opening is stored here.

## Result

| Test | Result | Evidence |
| --- | --- | --- |
| 4. Recent-root boundaries and reorg | Pass at the EL rule and eviction level | A live private spend accepted a root at exactly `S+1`. Focused tests on the deployed source revision accepted `S+1` and `S+8191`, rejected `S` and `S+8192`, rejected a missing fork entry, and evicted a pending transaction after a head change removed its entry. A full CL-driven reorg and Engine payload rejection was not run because the authenticated Engine endpoint is private. |
| 5. Profile 2 classification | Classification and omission gates pass on the deployed source revision | The exact live privacy envelope is a no-blob `self_verify` candidate costing 352,800 VERIFY gas, below `2**20`. Blob, extra-VERIFY, and `2**20 + 1` variants were ineligible and their omission produced no unsatisfied verdict. Slot 4 and unrelated-account storage reads were outside the configured four-slot surface; a stateful surface failure also excused omission. A live Engine replay through the stateful evaluator was unavailable. |
| 6. Public gossip versus direct includer | Public gossip passes; direct path unavailable | A proof-heavy private transaction sent only to `rpc1` appeared pending on the independent `rpc2` and `rpc3` nodes after about 1.63 seconds, before inclusion. All three public endpoints reject Engine methods as unavailable, so direct includer delivery cannot be tested without operator access. |

## Live `S+1` private spend

`republishRoot()` committed the unchanged pool root in transaction
`0x27b6704dd93cfb1a96737d8285abcdb82169daf8074836ce3303aa0db6d6a3d3`
at [slot 11297](https://dora.hegota.ethrex.xyz/slot/11297). The private spend
declared that exact root tuple and mined in
[slot 11298](https://dora.hegota.ethrex.xyz/slot/11298):

| Field | Value |
| --- | --- |
| Transaction | `0x01f4ba404173cb5d5128453ca3e26a80bd705ad534c8cb5b191fcbf6e1f31b88` |
| Root slot | 11,297 |
| Inclusion slot | 11,298 |
| Slot delta | 1 |
| Type and shape | `0x06`, `SelfVerify` |
| VERIFY budget | 350,000 frame gas + 2,800 signature gas = 352,800 |
| Receipt | status `1`, both frames status `1`, 1,377,511 gas |
| Sender and payer | pool, self-paying |

The transaction consumed its two keyed nullifiers, appended leaves 11 and 12,
and advanced `nextIndex` from 11 to 13. All three nodes later returned the same
receipt and block hash after more than 80 descendants.

The 0.006 ETH input note was disposable. Its new output openings were not
persisted, so the 0.0055 ETH outputs are intentionally unrecoverable by this
harness. The 0.0005 ETH proof-bound fee remained in the pool.

## Public propagation

The transaction was submitted only through `rpc1`. Polling began immediately.
`rpc2` returned the transaction with `blockHash = null` after 1,626.9 ms, and
`rpc3` did so after 1,627.9 ms. Both observations happened before the transaction
mined. The three endpoints expose different node IDs through `admin_nodeInfo`, so
this is evidence of peer propagation rather than three aliases for one EL process.

The public endpoints do not expose `engine_getInclusionListV1` or
`engine_exchangeCapabilities`; each returned JSON-RPC error `-32601`. This run
therefore does not claim direct includer delivery or an end-to-end omitted-IL
verdict.

## Exact-revision tests

Focused tests were compiled into a clean clone at commit `b23e206` and run with:

```text
cargo test -p ethrex-blockchain recent_root_conformance_test
cargo test -p ethrex-blockchain profile_2_conformance_test
cargo test -p ethrex-levm focil_surface_conformance_test
```

All four assertion groups passed. The recent-root test seeded the protocol
predeploy with one exact entry, exercised ages 0, 1, 8191, and 8192, then put a
transaction naming an absent entry into the mempool and confirmed
`revalidate_frame_txs_after_block` removed it. The Profile 2 test decoded the
exact live raw transaction, classified its negative variants, and ran each
through `check_with_profile_2`. The storage test checked the production
`within_vops_surface` predicate at slots 3 and 4 for sender, payer, and an
unrelated account.

The consensus execution path in `crates/vm/levm/src/vm.rs` performs the same
recent-root age and storage checks before any frame executes and returns a
transaction-validation error on failure. A complete `engine_newPayloadV6`
reorg test remains the final end-to-end confirmation.

## Conclusion

The deployed revision handles the recent-root window, reorg eviction, and the
Profile 2 classification and omission gates as expected in the tested EL paths.
Public gossip of the proof-heavy privacy transaction is now directly
demonstrated across three EL nodes. Operator access to an authenticated
includer or Engine endpoint is still required for the direct-delivery half of
test 6 and for a full consensus-client reorg test.
