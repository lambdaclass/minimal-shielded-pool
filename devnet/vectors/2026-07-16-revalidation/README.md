# Self-paying flow revalidation: live ethrex run

Date: 2026-07-16
Chain: Hegotá ethrex devnet, chain ID `3151908`
Endpoint: `https://rpc1.hegota.ethrex.xyz`
Source: hardness commit `e75770b` working tree (plus the diagnostic-message fix
to `pool_frametx.py` landed with this bundle)

This is an independent end-to-end re-run of the default two-frame self-paying
path, one day after the July 15 closure, from a fresh disposable deployer and
a fresh zero-balance outer signer. It also closes the fresh-note adversarial
negatives for the current tree state on a second pool.

## Deployment (pool 1, lifecycle)

- Pool / dispatcher: `0xf6f36f015764db4685cd1aa924eea156e02fd19b`
- Logic: `0x6b1572A8bd6177fcEEfe66F7AA6281400C6E217B`
- Envelope probe: `0xdf38461779005ac984e5f6a3ce7739c9c45a943e`
- Groth16 verifier (reused, byte-equal to current build): `0x712516e61C8B383dF4A63CFe83d7701Bce54B03e`
- Poseidon T3 / T4 (reused): `0xbCF26943C0197d2eE0E5D05c716Be60cc2761508` / `0x59F2f1fCfE2474fD5F0b9BA1E73ca90b143Eb8d0`
- Source ID: `0x08ef30269b17fc5c8d063e58f4094c677062c53e9d71bd29de889ba230f67811`
- Outer signer (zero balance throughout): `0xD9C696D473154791B838A19762D04DE5529b439c`
- Paymaster: none

| Action | Transaction | Block | Gas used | Payer |
|---|---|---:|---:|---|
| Shield 1 ETH | `0x2e362a4ac1ec044a528330254c88f224bdba2a95b6833debbebca06a188334c8` | 203728 | 1,083,142 | depositor |
| Private transfer | `0xc6afbe6a856ebf56e03bf03b917ea645088608293bfa764c50bab9b6b7671224` | 203732 | 1,385,462 | pool |
| Private withdrawal | `0x927f8147309802c8fda241337dc060dd859d9bb3d721f42134d5dff673468a80` | 203735 | 1,525,580 | pool |
| Claim withdrawal | block 203747 | 203747 | 35,538 | ordinary caller |

Both spends decoded to exactly two frames, `VERIFY(pool, execution + payment)`
then `SENDER(pool)`, and resolved `sender == payer == pool`. Post-state was
exact: 0.55 ETH withdrawal credit for `0x…cafebabe` (claimed and cleared),
`feeCredit(pool) == 0`, final pool balance `997,088,957,979,622,706 wei`
(the 1 ETH shield minus exactly 2,911,042 gas of self-paid debits at the
1.000000007 gwei effective price). Re-publishing either exact mined
transaction (`transfer.raw`, `withdraw.raw`) was rejected at admission:

```text
Nonce mismatch: expected 1, got 0
```

## Adversarial negatives (pool 2, fresh note)

A second dispatcher was deployed at
`0x9cac467d7cb63a084680e78af027775b854e3ebe` (source ID
`0xee4709879a659009eb344253391f9a4fae42dc44384999ff0340148d1e29fa3c`),
sharing the immutable logic and verifier, with a randomized-secret fixture
(`smoke_fixture.adv.json`). Its 0.1 ETH shield mined as `0xbd0da6f6…`
(block 204239). Against that unconsumed note:

- Flipped proof bit: dry-run simulation reports `validation prefix frame
  reverted`; no payer resolved, nothing consumed.
- Settlement frame down-gassed to 5,000,000: same prefix rejection.
- The unmodified spend then mined: `0x84d2ed4325c5c3f20314e1552621cdcc0270301605dea35512daff85f6b5ce02`
  (block 204246, 1,385,474 gas, self-paid).

Two tooling notes from the run: a second deterministic (fixed-seed) fixture
against an already-used deployment reuses the dummy note, whose nullifier is an
already-consumed keyed nonce, so adversarial fixtures on shared deployments
must use `--random` or a fresh pool; and the recent-root self-check's
diagnostic previously blamed slot derivation for what is usually a fixture
root mismatch against a non-empty tree (fixed in `pool_frametx.py` with this
bundle).

## Artifacts

- `deploy_config.json`, `smoke_fixture.json`: pool 1 lifecycle.
- `deploy_config.adv.json`, `smoke_fixture.adv.json`: pool 2 negatives.
- `transfer.raw`, `withdraw.raw`: exact mined type-`0x06` transactions.
- `SHA256SUMS`: integrity hashes.
