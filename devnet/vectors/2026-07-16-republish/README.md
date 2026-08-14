# republishRoot(): live validation

Date: 2026-07-16
Chain: Hegotá ethrex devnet, chain ID `3151908`
Endpoint: `https://rpc1.hegota.ethrex.xyz`

Validates the new permissionless `republishRoot()` entrypoint, added after the
same-day live run hit the quiet-pool deadlock: the recent-root entry for an
unspent note aged past the 8191-slot window, and with publication happening
only on insert there was no way to restore a referenceable entry without
changing the tree.

## Deployment

- Pool / dispatcher: `0x3490ce6f417f56a7e410e43b7edacb00ca298e5e`
- Logic (with republishRoot): `0xD45e547DDBfFf76D9439A791CE04b13b35ACeFF2`
- Probe / verifier / Poseidon libraries reused from the same-day revalidation
  deployment.
- Source ID: `0xd37a5b28eb7bf8179400184aa38fa55e4ca6710685756ffb0cc12a6efd50ffaa`

## Sequence

| Action | Transaction | Block | Gas used |
|---|---|---:|---:|
| Shield 0.1 ETH | `0xec1607aeb912b37c96a6ee1bd940ff90dbc17dd9e33304059ff6d0806e6c2233` | 217910 | 1,083,118 |
| republishRoot() | ordinary call from the disposable deployer | 217913 | 52,680 |
| Private transfer | `0xe60fb3f6753d0a1d0dd9b10dedca362eeae01cd4132083a49d38588e6db3dca0` | 217917 | 1,385,424 |

The republish emitted `RootRepublished(bytes32 indexed root, uint64 slot)`
(topic `0x89a7a65c…`) with the unchanged current root and block 217913, and
wrote the same 64-byte `SALT || root` payload to the predeploy. The transfer's
declared recent-root reference then named slot 217913, the entry created by
the republish rather than by any insert, and the two-frame self-paying spend
mined through the public mempool with the pool as sender and payer and a
zero-balance outer signer. This closes the loop the morning's failure opened:
a quiet pool is revived by anyone re-stamping its root, and a proof built
against that root spends without regeneration.

## Artifacts

- `deploy_config.json`: pool 3 addresses; `_slot_transfer` is the republish
  block, not the shield block.
- `smoke_fixture.json`: deployment-bound proofs.
- `transfer.raw`: the exact mined type-`0x06` transfer.
- `SHA256SUMS`: integrity hashes.
