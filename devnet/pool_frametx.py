#!/usr/bin/env python3
"""Build and submit the minimal pool's EIP-8141 FrameTxs on ethrex.

Spends use one exact grammar:

  VERIFY(pool, proof, execution+payment) -> SENDER(pool, settle(Spend))

The pool is sender and payer. EIP-8250 keys are the two proof nullifiers. The
sole secp256k1 signature comes from the fresh authorizer selected by the proof,
so it binds the complete transaction, including proof bytes, gas, fees and the
exact EIP-8272 reference. The reference slot is read from EIP-7843
`slotNumber`; timestamp derivation is intentionally unsupported.

Usage (append --dry-run to simulate without submitting):
  pool_frametx.py <rpc> config.json fixture.json shield   <funded-private-key>
  pool_frametx.py <rpc> config.json fixture.json transfer <unused>
  pool_frametx.py <rpc> config.json fixture.json withdraw <unused>

Spend signing keys come from the fixture's proof-bound
`authorizer_private_key`. `--root-slot N` supplies the consensus slot in which
`publishEpochRoot(epoch)` committed the root. Negative-vector flags include
`--flip-proof`, `--nonce-keys`, `--settle-gas`, and `--sender`.
"""
import json
import subprocess
import sys
import time
import urllib.request

from eth_keys import keys
from frametx import Frame, FrameSig, FrameTx


SPEND_TUPLE = "(bytes32,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint256,address,address)"
VERIFY_FRAME_GAS = 320_000
SETTLE_FRAME_GAS = 2_000_000


def rpc(url, method, params):
    req = urllib.request.Request(
        url, headers={"content-type": "application/json"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
    r = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if "error" in r:
        raise RuntimeError(f"{method} -> {r['error']}")
    return r["result"]


def simulate(url, raw):
    """Dry-run a built frame tx via ethrex_simulateFrameTransaction (the
    ethrex_ namespace, ethrex >= v17, commit e7e495f): the frame-native
    counterpart to eth_estimateGas, which cannot represent a multi-frame tx.
    Runs the mempool validation prefix and, if it passes, a full read-only
    multi-frame execution at head. Returns the result dict, or None if the
    endpoint does not expose the method (-32601). Raises on any other RPC
    error (malformed tx). A tx over the per-tx gas cap comes back as a result
    with valid=False, not an error."""
    req = urllib.request.Request(
        url, headers={"content-type": "application/json"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1,
                         "method": "ethrex_simulateFrameTransaction",
                         "params": [raw]}).encode())
    r = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if "error" in r:
        if r["error"].get("code") == -32601:
            return None
        raise SystemExit(f"  simulate RPC error: {r['error']}")
    return r["result"]


def cast_calldata(sig, *args):
    """Build ABI calldata with foundry's cast (correct for the nested Spend
    struct without hand-rolling an ABI encoder)."""
    out = subprocess.run(["cast", "calldata", sig, *[str(a) for a in args]],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"cast calldata failed: {out.stderr}")
    return bytes.fromhex(out.stdout.strip().removeprefix("0x"))


def spend_args(entry):
    """The publics-only Spend tuple literal (for cast) from a fixture entry."""
    return (f'({entry["root"]},{entry["root_slot"]},{entry["epoch"]},'
            f'{entry["domain"]},{entry["nf1"]},{entry["nf2"]},'
            f'{entry["out_cm1"]},{entry["out_cm2"]},{entry["public_amount"]},'
            f'{entry["fee"]},{entry["recipient"]},{entry["authorizer"]})')


def proof_bytes(entry):
    """The raw 256-byte proof (pA || pB || pC in snarkjs calldata word order):
    frame 0's calldata. The frame-0 verifier reads these eight words directly;
    settlement never carries them."""
    p = entry["proof"]
    words = [p["pA"][0], p["pA"][1], p["pB"][0][0], p["pB"][0][1],
             p["pB"][1][0], p["pB"][1][1], p["pC"][0], p["pC"][1]]
    return b"".join(int(w, 16).to_bytes(32, "big") for w in words)


RECENT_ROOT_ADDRESS = "0x0000000000000000000000000000000000008272"
RECENT_ROOT_LENGTH = 8192


def _keccak(b):
    from eth_hash.auto import keccak
    return keccak(b)


def recent_root_ref(url, cfg, e):
    """Encode and locally verify the exact EIP-8272 reference.

    The slot is the consensus `slotNumber` returned by EIP-7843. It is never
    reconstructed from timestamps. The epoch selects the pool's deterministic
    EIP-8272 source while the nullifier domain remains stable across epochs.
    """
    from frametx import rlp_bytes, rlp_int, rlp_list
    slot = int(e["root_slot"])
    epoch = int(e["epoch"])
    pool = int(cfg["pool"], 16)
    source_id = _keccak(pool.to_bytes(20, "big") + epoch.to_bytes(32, "big"))
    root = bytes.fromhex(e["root"].removeprefix("0x"))

    head = rpc(url, "eth_getBlockByNumber", ["latest", False])
    if "slotNumber" not in head:
        raise SystemExit("latest block has no EIP-7843 slotNumber; refusing timestamp derivation")
    now_slot = int(head["slotNumber"], 16)
    if now_slot - slot >= RECENT_ROOT_LENGTH:
        raise SystemExit(
            f"  recent-root ref expired: publication slot {slot} is outside the "
            f"{RECENT_ROOT_LENGTH}-slot window at current slot {now_slot}. If the tree has not "
            f"changed since the proof's root, call publishEpochRoot({epoch}), read that block's "
            f"slotNumber, and re-sign with --root-slot set to that consensus slot.")

    # Self-check: the committed entry the protocol will validate against must
    # already exist for this (source_id, slot, root). One definition, shared
    # with RecentRootReference::{entry_hash, storage_key} in ethrex-common.
    entry = _keccak(_keccak(b"RECENT_ROOT_ENTRY") + source_id + slot.to_bytes(8, "big") + root)
    skey = _keccak(_keccak(b"RECENT_ROOT_STORAGE") + source_id + (slot % RECENT_ROOT_LENGTH).to_bytes(8, "big"))
    stored = rpc(url, "eth_getStorageAt", [RECENT_ROOT_ADDRESS, "0x" + skey.hex(), "latest"])
    if bytes.fromhex(stored.removeprefix("0x").rjust(64, "0")) != entry:
        raise SystemExit(
            f"  recent-root ref self-check failed at consensus slot {slot}. The "
            f"fixture root differs from the root committed at that slot (a fixture generated "
            f"against an empty tree cannot spend into a pool that already has leaves; regenerate "
            f"against a fresh deployment), or the wrong epoch/slot was supplied. Either would be "
            f"rejected as FrameTxRecentRootNotCommitted.")
    return rlp_list([rlp_bytes(source_id), rlp_int(slot), rlp_bytes(root)])


def build_and_send(url, pk, pool, value, calldata, protocol_nonces=None, proof_verify=None,
                   recent_root_refs=None, dry_run=False, sender_override=None,
                   max_fee_override=None, max_priority_override=None,
                   settle_gas_override=None, save_raw=None, frame0_data=b""):
    signer = int.from_bytes(pk.public_key.to_canonical_address(), "big")
    sender = sender_override if sender_override is not None else signer
    chain_id = int(rpc(url, "eth_chainId", []), 16)
    signer_address = pk.public_key.to_checksum_address()
    nonce_address = "0x" + sender.to_bytes(20, "big").hex()
    nonce = int(rpc(url, "eth_getTransactionCount", [nonce_address, "latest"]), 16)
    blk = rpc(url, "eth_getBlockByNumber", ["latest", False])
    base_fee = int(blk.get("baseFeePerGas", "0x0"), 16)
    max_priority = max_priority_override if max_priority_override is not None else 10**9
    max_fee = max_fee_override if max_fee_override is not None else base_fee * 2 + max_priority
    if max_fee < base_fee or max_priority > max_fee:
        raise SystemExit("fee overrides require max_fee >= base_fee and max_priority <= max_fee")
    nonce_keys = protocol_nonces if protocol_nonces else [0]
    nonce_seq = 0 if protocol_nonces else nonce

    def build(sender_gas=SETTLE_FRAME_GAS):
        if proof_verify:
            frames = [
                Frame(mode=1, flags=0x03, target=sender, gas_limit=VERIFY_FRAME_GAS,
                      value=0, data=frame0_data),
            ]
        else:
            # Ordinary shield shape: one lightweight self-verify frame approves
            # execution and payment, so the sender is its own payer. This is
            # separate from the proof-carrying spend profile above.
            frames = [Frame(mode=1, flags=0x03, target=sender, gas_limit=80_000, value=0, data=b"")]
        # The SENDER frame is not part of the capped prefix. It starts
        # fixed to the fork-scoped cap proved by the activation profile. An OOG
        # after payment approval burns the notes, so wallets may not resize a
        # spend below that immutable cap.
        frames.append(Frame(mode=2, flags=0, target=pool, gas_limit=sender_gas,
                            value=value, data=calldata))
        tx = FrameTx(
            chain_id=chain_id, nonce_keys=nonce_keys, nonce_seq=nonce_seq, sender=sender,
            frames=frames,
            signatures=[FrameSig(FrameSig.SECP256K1, signer, b"", b"")],
            max_priority_fee=max_priority, max_fee=max_fee,
            recent_root_refs=recent_root_refs)
        s = pk.sign_msg_hash(tx.sig_hash())
        # EIP-8141 encodes the bare recovery id, 0 or 1. The legacy EVM
        # convention 27/28 is statically invalid for frame signatures.
        sig = bytes([s.v]) + s.r.to_bytes(32, "big") + s.s.to_bytes(32, "big")
        tx.signatures = [FrameSig(FrameSig.SECP256K1, signer, b"", sig)]
        return tx

    tx = build() if settle_gas_override is None else build(sender_gas=settle_gas_override)
    raw = "0x" + tx.raw().hex()
    if save_raw:
        with open(save_raw, "w") as f:
            f.write(raw)
    # Dry-run first: pre-check validity, report the resolved payer, and size
    # the (uncapped) SENDER frame from the simulated gas. Degrades to the
    # default limits on an endpoint that does not expose the ethrex_ namespace.
    sim = simulate(url, raw)
    if dry_run:
        if sim is None:
            print("  dry-run: ethrex_simulateFrameTransaction unavailable on this endpoint")
        else:
            per = ", ".join(f"f{i}={int(f['gasUsed'],16):,}" for i, f in enumerate(sim.get("frames") or []))
            g = int(sim["gasUsed"], 16) if sim.get("gasUsed") else None
            print(f"  dry-run: valid={sim.get('valid')}  shape={sim.get('prefixShape')}  "
                  f"payer={sim.get('payer')}  status={sim.get('executionStatus')}")
            print(f"           violation={sim.get('violation')}")
            print(f"           max_cost={tx.max_cost()}  total_gas_limit={tx.total_gas_limit()}")
            if g is not None:
                print(f"           gas={g:,}  ({per})")
        return
    eff = sim  # the simulation the send is gated on (resized one if adopted)
    if sim is None:
        # A spend that mines with a reverting SENDER frame still consumes its
        # nullifiers as protocol keyed nonces at payment approval but never
        # inserts the outputs: the notes are burned for good. The SENDER-revert
        # guard below is the only pre-send defense, so refuse to fly blind on
        # spends. A shield that reverts loses nothing (the deposit stays with
        # the sender), so shields may proceed on default limits.
        if protocol_nonces:
            raise SystemExit(
                "  simulate: ethrex_simulateFrameTransaction unavailable here; refusing to "
                "send a nullifier-consuming spend without a pre-send simulation "
                "(a mined tx whose SENDER frame reverts burns the spent notes)")
        print("  simulate: ethrex_simulateFrameTransaction unavailable here; default gas limits")
    elif sim.get("valid"):
        # gasUsed (top-level and per-frame) is a hex string on success, but the
        # node may return null; guard so a cosmetic gap never aborts a valid send.
        hexint = lambda v: int(v, 16) if isinstance(v, str) else None
        per = ", ".join(f"f{i}={hexint(f.get('gasUsed'))}" for i, f in enumerate(sim.get("frames") or []))
        total = hexint(sim.get("gasUsed"))
        print(f"  simulate: valid  shape={sim.get('prefixShape')}  payer={sim.get('payer')}  "
              f"gas={total}  ({per})")
        # Down-size the SENDER frame from the simulated gas ONLY for
        # non-spends. EIP-8037 state-dimension accounting varies 2-4x across
        # blocks, so measured + 25% is not a safe margin when the failure is
        # irreversible: a spend whose SENDER frame OOGs after payment approval
        # burns the notes (nullifiers consumed, outputs never inserted). For
        # spends the generous default stays; the payer's worst case is
        # prepaying more gas, refunded on success.
        used = hexint((sim.get("frames") or [{}])[-1].get("gasUsed"))
        if used is not None and not protocol_nonces:
            sized = used + used // 4  # measured + 25% for state-gas variance at a later block
            tx2 = build(sender_gas=sized)
            raw2 = "0x" + tx2.raw().hex()
            s2 = simulate(url, raw2)
            if s2 and s2.get("valid"):
                tx, raw, eff = tx2, raw2, s2
                print(f"  sized SENDER frame to {sized:,} gas (measured {used:,} + 25%)")
    else:
        # Since the 2026-07-08 devnet update, non-zero keyed nonces are
        # public-mempool admissible, so the faithful shape is expected to
        # simulate VALID. An invalid simulation is a real defect (insolvent
        # pool, wrong nonce keys, stale root, bad calldata), not the old
        # "expected inadmissibility": abort rather than broadcast a doomed tx
        # whose prefix failure the SENDER-revert guard below cannot catch.
        msg = f"  simulate: INVALID ({sim.get('violation')}); not sending"
        if protocol_nonces and "Nonce mismatch" in str(sim.get("violation", "")):
            msg += ("\n  a nullifier keyed nonce was already consumed. If this spend comes from a"
                    "\n  second deterministic fixture against an already-used deployment, the fixed"
                    "\n  seed reuses the dummy note and its nullifier collides; regenerate with"
                    "\n  gen_smoke.py --random or deploy a fresh pool.")
        raise SystemExit(msg)

    # Refuse to send when the SENDER frame reverts in simulation. Validation
    # should already reject a missing or too-recent EIP-8272 reference; this
    # separate gate protects against any application-level settlement failure.
    if eff and eff.get("executionStatus") and eff["executionStatus"] != "success":
        raise SystemExit(f"  simulate: SENDER frame reverts "
                         f"({eff.get('executionError') or eff['executionStatus']}); not sending "
                         "(if root-not-recent, retry one block later)")

    print(f"  frame tx: sender=0x{sender:040x} signer={signer_address} nonce_keys={nonce_keys} "
          f"raw_len={len(tx.raw())} max_cost={tx.max_cost()} sig_hash={tx.sig_hash().hex()[:18]}...")
    txhash = rpc(url, "eth_sendRawTransaction", [raw])
    print("  submitted:", txhash)
    for _ in range(30):
        rcpt = rpc(url, "eth_getTransactionReceipt", [txhash])
        if rcpt:
            status = int(rcpt.get('status', '0x0'), 16)
            print(f"  MINED block={int(rcpt['blockNumber'],16)} type={rcpt.get('type')} "
                  f"status={rcpt.get('status')} gasUsed={int(rcpt.get('gasUsed','0x0'),16)}")
            if status != 1:
                msg = f'  tx reverted (status {rcpt.get("status")}); aborting'
                if protocol_nonces:
                    msg += ("\n  WARNING: the SENDER frame reverted after payment approval, so the"
                            "\n  nullifiers were consumed as protocol keyed nonces and the spent"
                            "\n  notes are burned; the output notes were never inserted.")
                raise SystemExit(msg)
            return rcpt
        time.sleep(2)
    raise SystemExit("  not mined within timeout")


def main():
    url, cfg_path, fix_path, op, priv = sys.argv[1:6]
    cfg = json.loads(open(cfg_path).read())
    fix = json.loads(open(fix_path).read())
    pool = int(cfg["pool"], 16)
    pk = keys.PrivateKey(bytes.fromhex(priv.removeprefix("0x")))
    dry = "--dry-run" in sys.argv
    sender_override = None
    if "--sender" in sys.argv:
        i = sys.argv.index("--sender")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--sender requires an address")
        try:
            sender_override = int(sys.argv[i + 1], 16)
        except ValueError:
            raise SystemExit(f"invalid sender address: {sys.argv[i + 1]}") from None
        if sender_override == 0 or sender_override >= 1 << 160:
            raise SystemExit(f"invalid sender address: {sys.argv[i + 1]}")
    max_fee_override = None
    max_priority_override = None
    settle_gas_override = None
    save_raw = None
    nonce_keys_override = None
    note_index = None
    spend_key_override = None
    root_slot_override = None
    flip_proof = "--flip-proof" in sys.argv
    if "--note" in sys.argv:
        i = sys.argv.index("--note")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--note requires an index into fixture['shields']")
        note_index = int(sys.argv[i + 1], 0)
    if "--spend-key" in sys.argv:
        i = sys.argv.index("--spend-key")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--spend-key requires a fixture key (e.g. transfer_c)")
        spend_key_override = sys.argv[i + 1]
    if "--root-slot" in sys.argv:
        i = sys.argv.index("--root-slot")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--root-slot requires the block number that published the root")
        root_slot_override = int(sys.argv[i + 1], 0)
    if "--settle-gas" in sys.argv:
        i = sys.argv.index("--settle-gas")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--settle-gas requires a gas value")
        settle_gas_override = int(sys.argv[i + 1], 0)
    if "--save-raw" in sys.argv:
        i = sys.argv.index("--save-raw")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--save-raw requires a path")
        save_raw = sys.argv[i + 1]
    if "--nonce-keys" in sys.argv:
        i = sys.argv.index("--nonce-keys")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--nonce-keys requires 0x..,0x..")
        nonce_keys_override = sorted(int(x, 16) for x in sys.argv[i + 1].split(","))
        if len(nonce_keys_override) != 2:
            raise SystemExit("--nonce-keys requires exactly two keys")
    for flag, target in (("--max-fee-per-gas", "max_fee"),
                         ("--max-priority-fee-per-gas", "max_priority")):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 >= len(sys.argv):
                raise SystemExit(f"{flag} requires a wei value")
            try:
                value = int(sys.argv[i + 1], 0)
            except ValueError:
                raise SystemExit(f"invalid {flag} value: {sys.argv[i + 1]}") from None
            if value < 0:
                raise SystemExit(f"{flag} must be non-negative")
            if target == "max_fee":
                max_fee_override = value
            else:
                max_priority_override = value
    if op in ("transfer", "withdraw"):
        if sender_override is None:
            sender_override = pool

    def spend_setup(op_name):
        """Protocol nonces, validation data, and recent-root reference for a
        settle-only spend. The proof-selected one-time signer authorizes the
        complete immutable two-frame transaction.

        `--spend-key KEY` reads the spend entry from fix[KEY] instead of
        fix[op_name] (the nonce-race fixture carries two transfers, `transfer`
        and `transfer_c`, against one shared root). `--root-slot N` overrides
        the recent-root publication block: both race transfers bind the SAME
        root R, so they share the block where the second shield completed the
        tree, rather than distinct cfg _slot_transfer/_slot_withdraw values."""
        fix_key = spend_key_override if spend_key_override is not None else op_name
        # Copy before adversarial mutation so the loaded fixture remains an
        # immutable source of truth for subsequent operations in this process.
        e = json.loads(json.dumps(fix[fix_key]))
        if flip_proof:
            e["proof"]["pA"][0] = hex(int(e["proof"]["pA"][0], 16) ^ 1)
        protocol_nonces = sorted([int(e["nf1"], 16), int(e["nf2"], 16)])  # strictly increasing
        slot = root_slot_override if root_slot_override is not None else cfg[f"_slot_{op_name}"]
        e["root_slot"] = str(slot)
        refs = [recent_root_ref(url, cfg, e)]
        auth_pk = keys.PrivateKey(bytes.fromhex(e["authorizer_private_key"].removeprefix("0x")))
        if auth_pk.public_key.to_checksum_address().lower() != e["authorizer"].lower():
            raise SystemExit("fixture authorizer private key does not match the proof public")
        return e, protocol_nonces, True, refs, auth_pk

    if op == "shield":
        if "shields" in fix:
            # nonce-race fixture: shield the note at --note N from the shields
            # array (both notes go into one tree; the second shield publishes
            # the shared root R the two race transfers reference).
            if note_index is None:
                raise SystemExit("this fixture has a 'shields' array; pass --note N (0-based)")
            s = fix["shields"][note_index]
            value, inner = int(s["value"]), s["inner"]
        else:
            value, inner = int(fix["shield_value"]), fix["inner_a"]
        calldata = cast_calldata("shield(bytes32)", inner)
        print(f"shield {value} wei via frame tx -> pool {cfg['pool']}")
        build_and_send(url, pk, pool, value, calldata, dry_run=dry)
    elif op == "transfer":
        e, protocol_nonces, verify, refs, auth_pk = spend_setup("transfer")
        if nonce_keys_override is not None:
            protocol_nonces = nonce_keys_override
        calldata = cast_calldata(f"settle({SPEND_TUPLE})", spend_args(e))
        print(f"join-split transfer via frame tx (pool {cfg['pool']} self-pays)")
        build_and_send(url, auth_pk, pool, 0, calldata, protocol_nonces, verify, refs,
                       dry_run=dry, sender_override=sender_override,
                       max_fee_override=max_fee_override, max_priority_override=max_priority_override,
                       settle_gas_override=settle_gas_override, save_raw=save_raw,
                       frame0_data=proof_bytes(e))
    elif op == "withdraw":
        e, protocol_nonces, verify, refs, auth_pk = spend_setup("withdraw")
        if nonce_keys_override is not None:
            protocol_nonces = nonce_keys_override
        calldata = cast_calldata(f"settle({SPEND_TUPLE})", spend_args(e))
        print(f"join-split withdraw via frame tx (pool {cfg['pool']} self-pays)")
        build_and_send(url, auth_pk, pool, 0, calldata, protocol_nonces, verify, refs,
                       dry_run=dry, sender_override=sender_override,
                       max_fee_override=max_fee_override, max_priority_override=max_priority_override,
                       settle_gas_override=settle_gas_override, save_raw=save_raw,
                       frame0_data=proof_bytes(e))
    else:
        raise SystemExit(f"unknown op {op}")


if __name__ == "__main__":
    main()
