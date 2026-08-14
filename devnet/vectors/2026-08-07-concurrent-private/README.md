# Concurrent private transactions on Hegotá (2026-08-07)

## Target

The public devnet reported chain ID `3151908` and
`ethrex/v23.0.0-HEAD-b23e2068475231f2f5c8dd7bf25d916370e9024e`.
The test used dispatcher pool `0x593da3f0c8587af52fab28008422d2f526703cdb`
with source ID
`0xe21fc6e5b95c64f7533dca997009638aa03f7325a024597b62e9aada4aad880b`.
No private key or note opening is stored here.

## Evaluation contract

Two Groth16 transfers must use the same pool sender, the same recent root, and
`nonce_seq = 0`, while consuming disjoint EIP-8250 key sets. Both must pass
`ethrex_simulateFrameTransaction`, be pending at the same time, and mine with
successful frame and transaction receipts. Each must append two output leaves.
Replaying either exact raw transaction must fail at keyed-nonce admission without
changing pool state.

The authoritative checks are the public RPC transaction and frame receipts, the
pool's `nextIndex` and `currentRoot`, and admission-level replay errors. Same-block
inclusion is a stronger observed result, not a requirement of keyed-nonce
correctness.

## Setup

The wallet's remaining balance was about `0.037 ETH`, so the first unbroadcast
1 ETH fixture was replaced with two `0.01 ETH` notes. This changes only the
disposable values. The concurrency structure is unchanged. Both proofs were
generated against one reconstructed live tree and independently verified with
the committed Groth16 verification key.

Two shield transactions inserted the inputs:

| Transaction | Block | Gas | Result |
| --- | ---: | ---: | --- |
| `0xc9c1c6d008a2da3c4e733353922fd015d4b1d58bb81a30019a99dd45ae6cb3d4` | 10,034 | 1,089,884 | type `0x06`, status `1` |
| `0x14c944aba107537ba5313970b5dcc0c4209bf76d76ed36cabbb8f8b0929c6ac4` | 10,039 | 922,823 | type `0x06`, status `1` |

The second shield published the shared root
`0x1e89ebeee3c4b218da8ddc08c7d95599ee794558f9205075589fa93dfea50f5c`.
Both spends simulated with `SelfVerify`, `payer == sender == pool`, successful
settlement, and no validation violation.

## Result

The two raw transactions were submitted through separate RPC requests in one
410.4 ms window. Both mined in [slot 10050](https://dora.hegota.ethrex.xyz/slot/10050),
which contained exactly these two execution transactions:

| Transaction | Index | Gas | Result |
| --- | ---: | ---: | --- |
| `0xd852fa1b76ddf0bc10049670e6fa40048cee42ddecbc9c2f93e3ace40b0e3fe7` | 0 | 1,341,529 | type `0x06`, status `1` |
| `0x5a4f9629ada5a7ad3d40e97dd8c9473b92ec2f1c19588763744d153790b93c05` | 1 | 1,508,488 | type `0x06`, status `1` |

Both RPC objects report `from = pool`, two frames, and `nonceSeq = 0`. Their
two-element `nonceKeys` sets are disjoint. The pool advanced from five leaves
to nine and ended at root
`0x1bdfca5c20259765c90a100abf80decf2a738bcba9b3677a00b58e033ce98a41`.
The receipts remained canonical eight blocks later.

Rebroadcasting either exact raw transaction returned:

```text
Nonce mismatch: expected 1, got 0
```

Both replays were rejected before execution. `nextIndex` remained `9` and the
root remained unchanged.

## Conclusion and limit

The deployed ethrex mempool and builder accept concurrent private transfers
from one pool sender when their keyed-nonce sets are disjoint. They can execute
in either order and, in this run, both landed in the same block at
`nonce_seq = 0`.

This is experimental evidence from one ethrex revision, one public endpoint,
and one builder configuration. It does not establish the policy of every
ethrex configuration or another client.
