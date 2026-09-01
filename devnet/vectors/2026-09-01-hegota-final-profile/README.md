# Frozen profile of the pre-relaunch Hegotá chain-8141 testnet

The record of the wire dialect and pool deployment this repository targeted before the
testnet's re-genesis onto the EIPs as currently specified. The re-genesis destroys the
chain, the deployed pool, and everything shielded in it, so nothing here is usable against
any live network — it exists so the deployment recorded in `deploy_config.json` can be
audited against source for as long as anyone cares to.

- `frametx.py` — the frozen envelope encoder (11 fields, one gas limit per frame,
  intrinsic 15000, `RECENTROOTREFLOAD` at `0xB5`, EIP-8250 TXPARAM ids one lower).
- `ShieldedPoolDispatcher.yul` + `build/shielded_pool_dispatcher_init.hex` — the
  dispatcher deployed at the pool address in `deploy_config.json`, and its compiled
  initcode. `cast code <pool>` on an archive node of the old chain reproduces from this
  source with solc 0.8.30 (`dispatcher.py --initcode`).
- `check_gas_profile.py` — the single-dimension gas bounds (settlement 2,000,000).
- `activation_manifest.testbed.json` — the fail-closed gate for this profile; its
  artifact paths point into this directory and it still validates:
  `python3 tooling/check_activation.py devnet/vectors/2026-09-01-hegota-final-profile/activation_manifest.testbed.json --allow-testbed`
- `deploy_config.json` — the live deployment record (addresses, roots, budgets).

Everything else the manifest pins (circuit, verifier, settlement logic) is unchanged by
the relaunch and stays at its normal path.
