# Partial overlap, claimed-index boundary, and approval-revert evidence (2026-08-07)

## Target

The public Hegotá endpoint reported chain ID `3151908` and
`ethrex/v23.0.0-HEAD-b23e2068475231f2f5c8dd7bf25d916370e9024e`.
The privacy tests used dispatcher pool
`0x593da3f0c8587af52fab28008422d2f526703cdb`. No private key or note opening
is stored here.

## Result

| Test | Result | Evidence |
| --- | --- | --- |
| Partial overlap `{a,b}` versus `{b,c}` | Pass | Both spends simulated valid against one parent root and shared exactly one nonce key. The first was admitted and the second was rejected as a pending overlap. The admitted spend mined successfully; both exact raws then failed with `Nonce mismatch: expected 1, got 0`. |
| Builder claimed index | Not live-wired | The underlying state transition has the required boundary: the omitted spend was valid before the conflicting spend and invalid after it. The exact ethrex commit's index-normalization helper passed its boundary test, but no claim reaches it and replay still uses end-of-payload state only. |
| Successful approval followed by SENDER revert | Pass, isolated probe | Frame 0 approved execution and payment, frame 1 reverted, the type-`0x06` receipt had status `0`, both keyed nonces advanced from 0 to 1, replay failed, and the block remained canonical. The privacy pool state did not change. |

## 1. Partial-overlap private spends

The existing pool root was republished without changing the tree in transaction
`0xc9391a1ec0408dc1866998a1baf74ed82646b051a1f400a0f85d70572e74d2de`
at block 11,038. Three unspent outputs from the preceding concurrent-private
run were recovered locally from its deterministic test seed. Two new Groth16
proofs spent leaves `{5,6}` and `{6,7}` against the same root. Their two-key
sets therefore intersected in exactly one key, and both used `nonce_seq = 0`.

Both raw transactions passed `ethrex_simulateFrameTransaction` before either
was sent. Concurrent submission admitted `{a,b}` and rejected `{b,c}` with:

```text
A pending frame transaction from this sender is already in the pool
```

The admitted transfer
`0xebf4cea48d3329b19cf1e9394b0de23d8629362e39223a577b39bb317be52f97`
mined in [slot 11061](https://dora.hegota.ethrex.xyz/slot/11061), status `1`,
using 1,341,471 gas. Its VERIFY and SENDER frames both succeeded. The pool's
`nextIndex` advanced from 9 to 11. Replaying either raw after inclusion failed
at admission with:

```text
Nonce mismatch: expected 1, got 0
```

This passes the EIP-8250 state and local-mempool property. The second spend was
rejected before block construction, so the result does not claim that a builder
accepted both conflicting transactions and discarded one during execution.

## 2. Claimed-index conflict

The live state transition supplies both required states. Before the winning
spend, the overlapping omitted spend passed full prefix and execution
simulation. After the winning spend, it failed on the shared keyed nonce. A
claim before the conflict should therefore make omission unjustified; a claim
after the conflict should justify it.

That verdict cannot yet be exercised through the deployed FOCIL path. In exact
commit `b23e206`, `evaluation_index` implements the range rule, and a focused
test passed claims at `0`, an interior index, and `len`, plus `None` and an
out-of-range value falling back to `len`. But the function has no caller. The
beacon block and Engine API carry no claimed index, and
`BlockchainProfile2Evaluator` opens only the block's final state root. It does
not reconstruct state at a claimed transaction index. Consequently the live
implementation always judges Profile 2 omissions at the end of the payload.

This is a real remaining ethrex item, not a failed privacy transaction. Claim
transport, fallback decoding, and per-index state reconstruction must be wired
before this test can pass end to end.

## 3. Post-approval SENDER revert

A disposable contract was deployed and funded in transaction
`0xd27834bda5b57fc25cae16c71c626e2818728136b7daaa2f1a8cb126d7b252c0`
at `0x801d03ad5a20dfefbe153c3b72a63d918298ec0b`. Its empty-calldata VERIFY path
calls combined execution-and-payment `APPROVE`; its non-empty SENDER path
reverts immediately.

The resulting transaction
`0x19a800b08aa7606acf11051e9d5b82ded84016466414f336033fb41f8d2e15f4`
mined in [slot 11094](https://dora.hegota.ethrex.xyz/slot/11094), used 61,256
gas, and had the expected top-level status `0`. Frame 0 succeeded using 40,027
gas and resolved the probe as payer. Frame 1 reverted using 23 gas. Both fresh
keyed-nonce slots changed from 0 to 1, exact replay failed with the expected
nonce mismatch, and the receipt still matched the canonical block after three
descendants. The privacy pool remained at 11 leaves with the same root.

This probe was used instead of burning another privacy note. The hardened pool
makes duplicate output insertion a no-op and binds a generous settlement gas
limit before approval. Its remaining operational post-approval failure is tree
exhaustion, which would require filling the depth-20 tree. The isolated probe
tests the same EIP-8141/EIP-8250 durability rule without manufacturing a pool
failure that the pool correctly prevents.

## Conclusion

Partial keyed-nonce overlap and post-approval durability both behave correctly
on the deployed ethrex revision. Claimed-index enforcement does not: the
underlying before/after state behavior is correct, but the claimed-index data
path and indexed replay are still missing.
