# Dora frame-tx rendering fixes: verification run

Date: 2026-07-17 (evening, UTC)
Chain: Hegotá ethrex devnet, chain ID `3151908`
Endpoint: `https://rpc1.hegota.ethrex.xyz`
Explorer build under test: `dora.hegota.ethrex.xyz` `git-1c3f6e23`

Not a protocol run: the transaction shapes are identical to
`2026-07-17-proof-in-verify` (split architecture, proof in the VERIFY frame,
publics-only settlement). The purpose was a fresh lifecycle to verify the five
Dora rendering bugs filed on 2026-07-16, after the operators confirmed fixes.
The nonce fix in particular is only visible on newly indexed transactions, and
the shield below was sent from an account at nonce 83 to make the field
observable.

## Deployment

- Pool / dispatcher: `0xf02a43985ab5011af94f6d4dad454c5e305a3e42`
- Logic: `0x389De1637Fb8866A9741Cb46Aa10bF6a38646D9a`
- Envelope probe: `0x00cfac4ff61d52771ef27d07c5b6f1263c2994a1`
- Groth16 verifier / Poseidon T3 / T4: reused immutables from prior runs
- Source ID: `0x704e65409d3a57eafbae815d73467d64e716dc1ffcd601fad11cc8be94a4b3a9`

Deploy note: `ShieldedPoolLogic` now costs 12,481,836 gas to deploy under
EIP-8037; the script's former 12M limit failed at the code deposit
(`0x9cf6bab7…`, status 0), and `run_live_dispatcher.sh` now passes 14M.

## Lifecycle (self-paying, spends at 0.4 gwei)

| Action | Transaction | Block | Gas used |
|---|---|---:|---:|
| Shield 0.1 ETH (seq 83) | `0x5a7537f679d2b32541e88204a84d2db33b63dca7839a417b8966d310e28bc3c8` | 228503 | 1,083,133 |
| Private transfer | `0xf0dc89644d6f3dfaa505a1fdc7553c7043f1ccf8681c6d037e2e53e6ab44d2cd` | 228524 | 1,384,704 |
| Private withdrawal | `0xae09b60e99defb43929412a0f68292bd52cad2c0aca6268db328f9260670fa69` | 228847 | 1,524,759 |

The spends passed `--max-fee-per-gas 400000000 --max-priority-fee-per-gas
400000000`: with the settle frame pinned at 10M gas, the default ~1 gwei max
fee makes `max_cost` ≈ 0.0104 ETH, above the fixture's 0.005 ETH proof-bound
fee, and the pool's VERIFY rejects the spend in simulation. At 0.4 gwei
`max_cost` is ≈ 0.00415 ETH and the same fixture clears. Post-state exact:
pool balance 0.098836… ETH equals the 0.1 ETH shield minus precisely the two
spends' gas at 0.4 gwei, with the 0.02 ETH withdrawal held as a recipient
credit.

## Explorer verdicts

Fixed in `git-1c3f6e23` (see `devnet/REVIEW.md` for the full pass):
block/receipt join (retroactive), Created Contract line removed (retroactive),
exact decimal value rendering, Nonce field wired to `nonce_seq`, and search
resolving frame transactions. Still open: no `/transactions` listing page,
search does not resolve transactions mined before the execution indexer, and
address pages index only top-level from/to so a shield does not appear on the
pool's address page.

## Artifacts

- `deploy_config.json`, `smoke_fixture.json`: the lifecycle pool.
- `transfer.raw`, `withdraw.raw`: exact mined type-`0x06` transactions.
- `SHA256SUMS`: integrity hashes.
