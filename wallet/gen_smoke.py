"""Generate the native-ETH join-split smoke fixture with REAL Groth16 proofs.

All values and fees are wei-denominated; no ERC-20 path is modeled. The story:

  1. Alice shields 1.0 ether into note A.
  2. Alice's join-split transfer: inputs (A, dummy), outputs (Bob 0.6,
     Alice's change 0.35), fee 0.05 retained by the self-paying pool. Two nullifiers,
     consumed on-chain as ONE EIP-8250 key set.
  3. Bob's withdraw: inputs (B, dummy), outputs (two canonical zero sinks),
     publicAmount 0.55 to the recipient, fee 0.05 to the sender.

The v2 circuit rejects the same note in both inputs directly (`nf1 != nf2`),
while the EIP-8250 duplicate-key rule remains defense in depth.

Each honest proof is verified off-chain against the committed verification
key before it lands in the fixture. Groth16 proving is randomised, so the
fixture pairs with the committed Groth16Verifier.sol from the same setup.

Run from the wallet/ directory:
  python3 gen_smoke.py [--random] [--chain-id=N] [--pool-address=0x...]
                       [--shield-wei=N] [--payment-wei=N] [--fee-wei=N]
                       [--output=PATH]

The value overrides preserve the same flow at a smaller scale. They are useful
for disposable devnet deployments and envelope boundary tests; defaults remain
1.0 ETH shielded, 0.6 ETH paid privately, and a 0.05 ETH fee.
"""
import json
import subprocess
import sys
from pathlib import Path

import wallet as w
from poseidon_bn254 import hex32

HERE = Path(__file__).parent
TOOLING = HERE.parent / "tooling"
BUILD = HERE.parent / "build"
WORK = HERE / "artifacts"
RECIPIENT = "0x00000000000000000000000000000000cafebabe"
ETH = 10**18
TEST_CHAIN_ID = 31337
# keccak256(pool20 || SALT) for the deterministic Forge-test pool address
# 0xf62849f9a0b5bf2913b396098f7c7019b51a820a and SALT = 0, matching EIP-8272.
TEST_POOL = "0xf62849f9a0b5bf2913b396098f7c7019b51a820a"


def run(cmd, cwd=TOOLING):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        print(r.stdout, r.stderr, file=sys.stderr)
        raise SystemExit(f"command failed: {' '.join(str(c) for c in cmd)}")
    return r


def prove(witness, tag):
    wpath = WORK / f"witness_{tag}.json"
    proofpath = WORK / f"proof_{tag}.json"
    pubpath = WORK / f"public_{tag}.json"
    wpath.write_text(json.dumps(witness))
    run(["npx", "snarkjs", "groth16", "fullprove", wpath,
         BUILD / "spend_js" / "spend.wasm", BUILD / "spend_final.zkey",
         proofpath, pubpath])
    # the real proof check, against the committed verification key
    run(["npx", "snarkjs", "groth16", "verify",
         HERE.parent / "contracts" / "vectors" / "spend_vkey.json", pubpath, proofpath])
    call = run(["npx", "snarkjs", "zkey", "export", "soliditycalldata", pubpath, proofpath])
    pa, pb, pc, _pub = json.loads("[" + call.stdout.strip() + "]")
    publics = [int(x) for x in json.loads(pubpath.read_text())]
    return publics, {"pA": pa, "pB": pb, "pC": pc}


def assert_unprovable(witness, tag):
    """Assert witness generation rejects a circuit-level attack."""
    wpath = WORK / f"review_{tag}.json"
    out = WORK / f"review_{tag}.wtns"
    wpath.write_text(json.dumps(witness))
    result = subprocess.run(
        ["npx", "snarkjs", "wtns", "calculate", BUILD / "spend_js" / "spend.wasm", wpath, out],
        capture_output=True, text=True, cwd=TOOLING,
    )
    if result.returncode == 0:
        raise SystemExit(f"UNSOUND: circuit accepted {tag}")


def spend_entry(
    tree, domain, inputs, outputs, epoch, public_amount, fee, recipient,
    authorizer, authorizer_private_key, publics, proof, **extra,
):
    root = tree.root()
    nf1, nf2 = w.input_nullifiers(domain, inputs)
    out_cm1, out_cm2 = w.output_commitments(outputs)
    # the crux: the proof's public signals are exactly the wallet's own
    # publics, in the circuit's order (outputs first, then public inputs),
    # which is also the order verifySpend passes them to the verifier
    assert publics == [nf1, nf2, out_cm1, out_cm2, root, domain,
                       public_amount, fee, recipient, authorizer], \
        "proof public signals do not bind the wallet's publics"
    e = {"root": hex32(root), "epoch": str(epoch),
         "domain": hex32(domain),
         "nf1": hex32(nf1), "nf2": hex32(nf2),
         "out_cm1": hex32(out_cm1), "out_cm2": hex32(out_cm2),
         "public_amount": str(public_amount), "fee": str(fee),
         "recipient": f"0x{recipient:040x}",
         "authorizer": f"0x{authorizer:040x}",
         "authorizer_private_key": authorizer_private_key,
         "proof": proof}
    e.update(extra)
    return e


def main():
    zkey = BUILD / "spend_final.zkey"
    if not zkey.exists():
        raise SystemExit("run the setup first: (cd ../tooling && ./setup.sh)")
    WORK.mkdir(exist_ok=True)
    if "--random" not in sys.argv:
        w.set_seed(20260702)
    chain_id = TEST_CHAIN_ID
    pool_address = TEST_POOL
    shield_wei = ETH
    payment_wei = ETH * 60 // 100
    fee_wei = ETH * 5 // 100
    output_path = HERE / "smoke_fixture.json"
    for arg in sys.argv[1:]:
        if arg.startswith("--chain-id="):
            chain_id = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--pool-address="):
            pool_address = arg.split("=", 1)[1]
        elif arg.startswith("--shield-wei="):
            shield_wei = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--payment-wei="):
            payment_wei = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--fee-wei="):
            fee_wei = int(arg.split("=", 1)[1], 0)
        elif arg.startswith("--output="):
            output_path = Path(arg.split("=", 1)[1]).expanduser().resolve()
    domain = w.domain_scalar(chain_id, pool_address)

    # notes: Alice's deposit, Bob's payment target, Alice's change target
    sk_a, rho_a = w.new_note()
    sk_b, rho_b = w.new_note()
    sk_a2, rho_a2 = w.new_note()
    v_shield, v_bob, v_fee = shield_wei, payment_wei, fee_wei
    if not 0 < v_fee < v_bob < v_shield:
        raise SystemExit("value overrides require 0 < fee < payment < shield")
    v_change = v_shield - v_bob - v_fee
    inner_a = w.inner(sk_a, rho_a)
    cm_a = w.commitment(sk_a, rho_a, v_shield)

    # 1+2. Alice's join-split transfer: (A, dummy) -> (Bob 0.6, change 0.35), fee 0.05
    t1 = w.Tree()
    t1.append(cm_a)
    ins_t = [{"sk": sk_a, "rho": rho_a, "value": v_shield, "idx": 0}, w.dummy_input()]
    outs_t = [(w.inner(sk_b, rho_b), v_bob), (w.inner(sk_a2, rho_a2), v_change)]
    auth_t_key, auth_t = w.new_authorizer()
    wt = w.build_witness(
        t1, ins_t, outs_t, domain, authorizer=auth_t, public_amount=0, fee=v_fee,
    )
    pub_t, proof_t = prove(wt, "transfer")

    # 3. Bob's withdraw: (B, dummy) -> (0, 0), publicAmount 0.55, fee 0.05
    t2 = w.Tree()
    for cm in [cm_a, *w.output_commitments(outs_t)]:
        t2.append(cm)
    cm_b = w.commitment(sk_b, rho_b, v_bob)
    assert t2.leaves[1] == cm_b, "Bob's note is leaf 1"
    ins_w = [{"sk": sk_b, "rho": rho_b, "value": v_bob, "idx": 1}, w.dummy_input()]
    outs_w = w.sink_outputs()
    v_pub = v_bob - v_fee
    auth_w_key, auth_w = w.new_authorizer()
    ww = w.build_witness(
        t2, ins_w, outs_w, domain, authorizer=auth_w,
        public_amount=v_pub, fee=v_fee, recipient=RECIPIENT,
    )
    pub_w, proof_w = prove(ww, "withdraw")

    # The old circuit accepted one real note in both inputs and relied only on
    # the envelope's duplicate-key rule. V2 rejects the witness itself.
    ins_same = [dict(ins_t[0]), dict(ins_t[0])]
    outs_same = [(w.inner(*w.new_note()), v_shield), (w.inner(*w.new_note()), v_shield)]
    _, auth_same = w.new_authorizer()
    same = w.build_witness(
        t1, ins_same, outs_same, domain, authorizer=auth_same,
        public_amount=0, fee=0,
    )
    assert_unprovable(same, "same_note")

    # A positive duplicate output was previously counted twice in conservation
    # but inserted once. The new relation rejects it before approval.
    duplicate_value = (v_shield - v_fee) // 2
    if duplicate_value * 2 + v_fee == v_shield:
        dup_inner = w.inner(*w.new_note())
        _, auth_dup = w.new_authorizer()
        duplicate = dict(wt)
        duplicate["out_inner"] = [str(dup_inner), str(dup_inner)]
        duplicate["out_value"] = [str(duplicate_value), str(duplicate_value)]
        duplicate["authorizer"] = str(auth_dup)
        assert_unprovable(duplicate, "duplicate_positive_output")

    no_real = dict(wt)
    no_real["in_value"] = ["0", "0"]
    no_real["out_inner"] = ["1", "2"]
    no_real["out_value"] = ["0", "0"]
    no_real["public_amount"] = "0"
    no_real["fee"] = "0"
    assert_unprovable(no_real, "zero_real_inputs")

    wrong_sinks = dict(ww)
    wrong_sinks["out_inner"] = ["2", "1"]
    assert_unprovable(wrong_sinks, "wrong_sink_positions")

    zero_authorizer = dict(wt)
    zero_authorizer["authorizer"] = "0"
    assert_unprovable(zero_authorizer, "zero_authorizer")

    positive_sink = dict(wt)
    positive_sink["out_inner"] = ["1", wt["out_inner"][1]]
    assert_unprovable(positive_sink, "positive_output_uses_sink")

    missing_recipient = dict(ww)
    missing_recipient["recipient"] = "0"
    assert_unprovable(missing_recipient, "withdrawal_without_recipient")

    fixture = {
        "chain_id": chain_id,
        "pool_address": pool_address,
        "domain": hex32(domain),
        "inner_a": hex32(inner_a),
        "cm_a": hex32(cm_a),
        "shield_value": str(v_shield),
        "recipient": RECIPIENT,
        "transfer": spend_entry(t1, domain, ins_t, outs_t, 0,
                                0, v_fee, 0, auth_t, auth_t_key, pub_t, proof_t,
                                # Bob's opening is retained for the withdrawal vector.
                                out_inner1=hex32(outs_t[0][0]),
                                out_value1=str(outs_t[0][1])),
        "withdraw": spend_entry(t2, domain, ins_w, outs_w, 0,
                                v_pub, v_fee, w.address_scalar(RECIPIENT),
                                auth_w, auth_w_key, pub_w, proof_w),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fixture, indent=1))
    print("real join-split proofs generated and verified off-chain; public signals bind the wallet publics")
    print(f"wrote {output_path}")
    print(f"  transfer  nf1 {fixture['transfer']['nf1'][:18]}... nf2 {fixture['transfer']['nf2'][:18]}... fee {v_fee}")
    print(f"  withdraw  publicAmount {v_pub} fee {v_fee}")
    print(f"  domain   {fixture['domain']} (chain {chain_id}, pool {pool_address})")
    print("  same-note witness rejected in-circuit (nf1 != nf2)")


if __name__ == "__main__":
    main()
