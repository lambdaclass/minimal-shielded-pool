"""Generate a fixture for the shared-sender nonce-race test.

Two notes A and C are shielded into ONE tree, so after both inserts the pool
publishes a single root R. Two independent transfers then prove membership
against that same R:

  transfer_a: spend A -> (Bob 0.6, change 0.35), fee 0.05
  transfer_c: spend C -> (Dave 0.6, change 0.35), fee 0.05

The two transfers consume DISJOINT nullifier sets (different notes), so under
EIP-8250 keyed nonces they share the shared sender's address without any
sequential-nonce ordering between them: both are admissible in the same block,
in any order. That is the property under test.

The fixture is shaped so pool_frametx.py can drive both spends: it exposes the
two transfers under the keys `transfer` (A) and a second entry the harness
reads directly. Both carry the same recent-root reference (R at R's slot).

Run from wallet/: python3 gen_nonce_race.py --chain-id=N --pool-address=0x...
                   --root-slot=N [--epoch=N]
                   [--output=PATH]
"""
import json
import sys
import urllib.request
from pathlib import Path

import wallet as w
from poseidon_bn254 import hex32
from gen_smoke import prove, spend_entry, ETH, WORK

HERE = Path(__file__).parent

# LeafAppended(bytes32 indexed cm, uint64 indexed epoch, uint32 index, bytes32 newRoot)
LEAF_APPENDED_TOPIC = "0x1c9386c619e61f45f16a19541b370266f8eb6fd22d241ff010e03cc31ea82368"


def _rpc(url, method, params):
    req = urllib.request.Request(
        url, headers={"content-type": "application/json"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in r:
        raise SystemExit(f"{method} -> {r['error']}")
    return r["result"]


def seeded_tree(url, pool):
    """Rebuild the pool's current Merkle tree from its LeafAppended events, in
    index order, and assert the reconstructed root equals the pool's on-chain
    currentRoot. A real wallet does this; without it the fixture's membership
    proofs would bind an empty-tree root that no live pool with prior leaves
    ever holds. The two new deposits then append at the next free leaves, so
    the root the pool computes after both shields is exactly the fixture's."""
    logs = _rpc(url, "eth_getLogs", [{"address": pool, "topics": [LEAF_APPENDED_TOPIC],
                                      "fromBlock": "0x0", "toBlock": "latest"}])
    epoch_word = _rpc(url, "eth_call", [{"to": pool, "data": "0x76671808"}, "latest"])
    epoch = int(epoch_word, 16)
    leaves = {}
    for l in logs:
        if int(l["topics"][2], 16) != epoch:
            continue
        idx = int(l["data"][2:][0:64], 16)
        leaves[idx] = int(l["topics"][1], 16)
    tree = w.Tree()
    for i in range(len(leaves)):
        tree.append(leaves[i])
    recon = hex32(tree.root())
    onchain = _rpc(url, "eth_call", [{"to": pool, "data": "0xfdab463d"}, "latest"])  # currentRoot()
    onchain = "0x" + onchain.removeprefix("0x").rjust(64, "0")
    if recon.lower() != onchain.lower():
        raise SystemExit(f"tree reconstruction mismatch: rebuilt {recon} != on-chain {onchain}; "
                         "the pool's leaf set changed or DEPTH/hash params differ")
    print(f"  seeded tree from {len(leaves)} on-chain leaves, root {recon[:18]}... verified")
    return tree


def main():
    chain_id = 31337
    pool_address = None
    root_slot = None
    epoch = 0
    note_wei = ETH
    rpc_url = None
    pool = None
    output_path = HERE / "nonce_race_fixture.json"
    for arg in sys.argv[1:]:
        if arg.startswith("--chain-id="):
            chain_id = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--pool-address="):
            pool_address = arg.split("=", 1)[1]
        elif arg.startswith("--root-slot="):
            root_slot = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--epoch="):
            epoch = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--note-wei="):
            note_wei = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--rpc="):
            rpc_url = arg.split("=", 1)[1]
        elif arg.startswith("--pool="):
            pool = arg.split("=", 1)[1]
        elif arg.startswith("--output="):
            output_path = Path(arg.split("=", 1)[1]).expanduser().resolve()
    if pool_address is None or root_slot is None:
        raise SystemExit("--pool-address=0x... and --root-slot=N are required")
    if (rpc_url is None) != (pool is None):
        raise SystemExit("--rpc= and --pool= must be given together (seed the live tree)")
    WORK.mkdir(exist_ok=True)
    w.set_seed(20260712)
    domain = w.domain_scalar(chain_id, pool_address)

    # Two deposits, into one tree. Root R is fixed after both inserts. Against a
    # live pool, seed the tree from its existing leaves first so the fixture's
    # root and proofs match the pool state after the two shields land.
    sk_a, rho_a = w.new_note()
    sk_c, rho_c = w.new_note()
    v = note_wei
    inner_a = w.inner(sk_a, rho_a)
    inner_c = w.inner(sk_c, rho_c)
    cm_a = w.commitment(sk_a, rho_a, v)
    cm_c = w.commitment(sk_c, rho_c, v)

    tree = seeded_tree(rpc_url, pool) if rpc_url else w.Tree()
    idx_a = tree.append(cm_a)
    idx_c = tree.append(cm_c)
    root_R = tree.root()

    v_bob, v_fee = v * 60 // 100, v * 5 // 100
    v_change = v - v_bob - v_fee

    # transfer A: spend note A (idx 0) against R
    sk_bob, rho_bob = w.new_note()
    sk_achg, rho_achg = w.new_note()
    ins_a = [{"sk": sk_a, "rho": rho_a, "value": v, "idx": idx_a}, w.dummy_input()]
    outs_a = [(w.inner(sk_bob, rho_bob), v_bob), (w.inner(sk_achg, rho_achg), v_change)]
    auth_a_key, auth_a = w.new_authorizer()
    wa = w.build_witness(
        tree, ins_a, outs_a, domain, authorizer=auth_a,
        public_amount=0, fee=v_fee,
    )
    pub_a, proof_a = prove(wa, "race_a")

    # transfer C: spend note C (idx 1) against the SAME R
    sk_dave, rho_dave = w.new_note()
    sk_cchg, rho_cchg = w.new_note()
    ins_c = [{"sk": sk_c, "rho": rho_c, "value": v, "idx": idx_c}, w.dummy_input()]
    outs_c = [(w.inner(sk_dave, rho_dave), v_bob), (w.inner(sk_cchg, rho_cchg), v_change)]
    auth_c_key, auth_c = w.new_authorizer()
    wc = w.build_witness(
        tree, ins_c, outs_c, domain, authorizer=auth_c,
        public_amount=0, fee=v_fee,
    )
    pub_c, proof_c = prove(wc, "race_c")

    ea = spend_entry(
        tree, domain, ins_a, outs_a, epoch, 0, v_fee, 0,
        auth_a, auth_a_key, pub_a, proof_a,
    )
    ec = spend_entry(
        tree, domain, ins_c, outs_c, epoch, 0, v_fee, 0,
        auth_c, auth_c_key, pub_c, proof_c,
    )

    nfa = {ea["nf1"], ea["nf2"]}
    nfc = {ec["nf1"], ec["nf2"]}
    assert nfa.isdisjoint(nfc), "transfers must consume disjoint nullifiers"
    assert ea["root"] == ec["root"] == hex32(root_R), "both transfers bind the same root"

    fixture = {
        "chain_id": chain_id,
        "pool_address": pool_address,
        "root_slot": root_slot,
        "epoch": epoch,
        "domain": hex32(domain),
        "root": hex32(root_R),
        "shields": [
            {"inner": hex32(inner_a), "cm": hex32(cm_a), "value": str(v), "leaf": idx_a},
            {"inner": hex32(inner_c), "cm": hex32(cm_c), "value": str(v), "leaf": idx_c},
        ],
        # pool_frametx.py reads spend entries under an op key; both are transfers
        "transfer": ea,
        "transfer_c": ec,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fixture, indent=1))
    print("two independent transfers proven against one root, disjoint nullifiers")
    print(f"  root R      {hex32(root_R)[:18]}...")
    print(f"  transfer A  nf {ea['nf1'][:14]}.. {ea['nf2'][:14]}..")
    print(f"  transfer C  nf {ec['nf1'][:14]}.. {ec['nf2'][:14]}..")
    print(f"  disjoint: {nfa.isdisjoint(nfc)}   same root: {ea['root'] == ec['root']}")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
