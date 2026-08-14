#!/usr/bin/env python3
"""Executable complete-intent binding vector for the one-time authorizer."""
import copy
import json
from pathlib import Path

from eth_hash.auto import keccak
from eth_keys import keys

from frametx import Frame, FrameSig, FrameTx, rlp_bytes, rlp_int, rlp_list
from pool_frametx import (
    SETTLE_FRAME_GAS,
    SPEND_TUPLE,
    VERIFY_FRAME_GAS,
    cast_calldata,
    proof_bytes,
    spend_args,
)

HERE = Path(__file__).parent
FIXTURE = HERE.parent / "wallet" / "smoke_fixture.json"


def root_ref(source, slot, root):
    return rlp_list([rlp_bytes(source), rlp_int(slot), rlp_bytes(root)])


def build():
    fixture = json.loads(FIXTURE.read_text())
    entry = copy.deepcopy(fixture["transfer"])
    entry["root_slot"] = "1"
    pool = int(fixture["pool_address"], 16)
    epoch = int(entry["epoch"])
    source = keccak(pool.to_bytes(20, "big") + epoch.to_bytes(32, "big"))
    root = bytes.fromhex(entry["root"][2:])
    settle = cast_calldata(f"settle({SPEND_TUPLE})", spend_args(entry))
    authorizer = int(entry["authorizer"], 16)
    pk = keys.PrivateKey(bytes.fromhex(entry["authorizer_private_key"][2:]))
    tx = FrameTx(
        chain_id=int(fixture["chain_id"]),
        nonce_keys=sorted([int(entry["nf1"], 16), int(entry["nf2"], 16)]),
        nonce_seq=0,
        sender=pool,
        frames=[
            Frame(1, 3, pool, VERIFY_FRAME_GAS, 0, proof_bytes(entry)),
            Frame(2, 0, pool, SETTLE_FRAME_GAS, 0, settle),
        ],
        signatures=[FrameSig(FrameSig.SECP256K1, authorizer, b"", b"")],
        max_priority_fee=1,
        max_fee=10,
        recent_root_refs=[root_ref(source, 1, root)],
    )
    sig = pk.sign_msg_hash(tx.sig_hash())
    encoded = bytes([sig.v]) + sig.r.to_bytes(32, "big") + sig.s.to_bytes(32, "big")
    tx.signatures[0].signature = encoded
    assert sig.recover_public_key_from_msg_hash(tx.sig_hash()).to_canonical_address() == authorizer.to_bytes(20, "big")
    return tx, authorizer


def main():
    tx, authorizer = build()
    original_hash = tx.sig_hash()
    original_signature = tx.signatures[0].signature

    mutations = []
    def add(name, fn):
        candidate = copy.deepcopy(tx)
        fn(candidate)
        mutations.append((name, candidate))

    add("chain_id", lambda x: setattr(x, "chain_id", x.chain_id + 1))
    add("nonce_key", lambda x: x.nonce_keys.__setitem__(0, x.nonce_keys[0] ^ 1))
    add("nonce_seq", lambda x: setattr(x, "nonce_seq", 1))
    add("sender", lambda x: setattr(x, "sender", x.sender ^ 1))
    add("verify_mode", lambda x: setattr(x.frames[0], "mode", 0))
    add("verify_flags", lambda x: setattr(x.frames[0], "flags", 2))
    add("verify_target", lambda x: setattr(x.frames[0], "target", x.frames[0].target ^ 1))
    add("verify_gas", lambda x: setattr(x.frames[0], "gas_limit", VERIFY_FRAME_GAS - 1))
    add("verify_value", lambda x: setattr(x.frames[0], "value", 1))
    for word in range(8):
        add(f"proof_word_{word}", lambda x, w=word: setattr(
            x.frames[0], "data", x.frames[0].data[:w * 32] +
            bytes([x.frames[0].data[w * 32] ^ 1]) + x.frames[0].data[w * 32 + 1:]))
    add("settle_mode", lambda x: setattr(x.frames[1], "mode", 1))
    add("settle_target", lambda x: setattr(x.frames[1], "target", x.frames[1].target ^ 1))
    add("settle_gas", lambda x: setattr(x.frames[1], "gas_limit", SETTLE_FRAME_GAS - 1))
    for word in range(12):
        add(f"settle_word_{word}", lambda x, w=word: setattr(
            x.frames[1], "data", x.frames[1].data[:4 + w * 32] +
            bytes([x.frames[1].data[4 + w * 32] ^ 1]) + x.frames[1].data[5 + w * 32:]))
    add("signature_scheme", lambda x: setattr(x.signatures[0], "scheme", 2))
    add("signature_signer", lambda x: setattr(x.signatures[0], "signer", x.signatures[0].signer ^ 1))
    add("signature_message", lambda x: setattr(x.signatures[0], "msg", b"\x01" * 32))
    add("priority_fee", lambda x: setattr(x, "max_priority_fee", 2))
    add("max_fee", lambda x: setattr(x, "max_fee", 11))
    add("blob_fee", lambda x: setattr(x, "max_blob_fee", 1))
    add("blob_hashes", lambda x: x.blob_hashes.append(b"\x01" * 32))
    add("root_source", lambda x: x.recent_root_refs.__setitem__(0, root_ref(b"\x01" * 32, 1, b"\x02" * 32)))
    add("root_slot", lambda x: x.recent_root_refs.__setitem__(0, root_ref(b"\x01" * 32, 2, b"\x02" * 32)))
    add("root_value", lambda x: x.recent_root_refs.__setitem__(0, root_ref(b"\x01" * 32, 1, b"\x03" * 32)))

    for name, candidate in mutations:
        assert candidate.sig_hash() != original_hash, f"signature hash did not bind {name}"
        sig = keys.Signature(vrs=(original_signature[0],
                                  int.from_bytes(original_signature[1:33], "big"),
                                  int.from_bytes(original_signature[33:65], "big")))
        recovered = sig.recover_public_key_from_msg_hash(candidate.sig_hash()).to_canonical_address()
        assert recovered != authorizer.to_bytes(20, "big"), f"old signature authorized {name}"

    raw_changed = copy.deepcopy(tx)
    raw_changed.signatures[0].signature = bytes([original_signature[0]]) + bytes([original_signature[1] ^ 1]) + original_signature[2:]
    assert raw_changed.sig_hash() == original_hash, "empty-msg raw signature bytes must be elided"

    print(json.dumps({"bound_mutations": len(mutations),
                      "raw_signature_elision_only": True,
                      "proof_bytes_bound": True,
                      "settlement_words_bound": 12}, sort_keys=True))


if __name__ == "__main__":
    main()
