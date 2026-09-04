#!/usr/bin/env python3
"""EIP-8141 FrameTx (type 0x06) encoder, at the spec's current pin.

The envelope as published at ethereum/EIPs `7d1c8bfb94` and implemented by ethrex. The
dialect the pre-relaunch chain-8141
deployment uses; the two envelopes are mutually unreadable and each needs its own encoder,
dispatcher, and gas profile is archived byte-exact under
`devnet/vectors/2026-09-01-hegota-final-profile/`, the record of what was deployed.

What changed, and why each matters to a signer:

  * the fee fields nest into one `fees` list, taking the envelope from 11 fields to 9, so
    every signature hash differs;
  * a frame declares `limits = [execution, state]` rather than one gas limit, because the spec
    meters EIP-8037 state gas as a second, separately declared dimension;
  * the intrinsic drops from 15000 to 12000, and a frame that moves value to another
    account adds `TX_VALUE_COST` (6000) apiece;
  * `standard_gas_limit` includes the declared state budgets, and the calldata-floor branch
    of `max_gas` adds them on top rather than absorbing them.

Wire layout:
  raw = 0x06 || rlp([chain_id, nonce_keys, nonce_seq, sender, frames, signatures,
                     fees, blob_hashes, recent_root_references])
  fees      = rlp([max_priority_fee, max_fee, max_blob_fee])
  frame     = rlp([mode, flags, target_or_empty, [execution, state], value, data])
  signature = rlp([scheme, signer, msg, signature_bytes])  # 0=ARBITRARY, 1=SECP256K1, 2=P256
  sig_hash  = keccak256(0x06 || rlp(envelope with empty-msg signatures' bytes elided))
"""
import pathlib
import sys
from eth_hash.auto import keccak

# ---------- minimal RLP ----------
def rlp_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80:
        return b
    if len(b) < 56:
        return bytes([0x80 + len(b)]) + b
    lb = len(b).to_bytes((len(b).bit_length() + 7) // 8, "big")
    return bytes([0xb7 + len(lb)]) + lb + b

def rlp_list(items) -> bytes:
    body = b"".join(items)
    if len(body) < 56:
        return bytes([0xc0 + len(body)]) + body
    lb = len(body).to_bytes((len(body).bit_length() + 7) // 8, "big")
    return bytes([0xf7 + len(lb)]) + lb + body

def rlp_int(x: int) -> bytes:
    if x == 0:
        return rlp_bytes(b"")
    return rlp_bytes(x.to_bytes((x.bit_length() + 7) // 8, "big"))

def addr20(a):  # (was int|bytes; widened for py3.9)
    if isinstance(a, int):
        return a.to_bytes(20, "big")
    return bytes(a)

def rlp_items(encoded: bytes):
    """Split one RLP list into its top-level items, each still RLP-encoded.

    Enough to assert an envelope's shape without a full decoder: the point is to count
    fields and look one level in, not to interpret them.
    """
    b = encoded
    if not b or b[0] < 0xC0:
        raise ValueError("not an RLP list")
    if b[0] <= 0xF7:
        body = b[1:1 + (b[0] - 0xC0)]
    else:
        n = b[0] - 0xF7
        body = b[1 + n:1 + n + int.from_bytes(b[1:1 + n], "big")]
    items, i = [], 0
    while i < len(body):
        p = body[i]
        if p < 0x80:
            size, head = 1, 0
        elif p <= 0xB7:
            size, head = p - 0x80, 1
        elif p <= 0xBF:
            n = p - 0xB7
            size, head = int.from_bytes(body[i + 1:i + 1 + n], "big"), 1 + n
        elif p <= 0xF7:
            size, head = p - 0xC0, 1
        else:
            n = p - 0xF7
            size, head = int.from_bytes(body[i + 1:i + 1 + n], "big"), 1 + n
        items.append(body[i:i + head + size])
        i += head + size
    return items


# ---------- frame-tx model ----------
class Frame:
    def __init__(self, mode, flags, target, gas_limit, value, data, state_limit=0):
        self.mode, self.flags, self.target = mode, flags, target  # target: 20-byte int/bytes or None
        self.gas_limit, self.value, self.data = gas_limit, value, data
        # `limits.state`: the frame's EIP-8037 state budget. A state charge past it halts the
        # frame with execution gas to spare, and it can never be borrowed from `gas_limit`.
        self.state_limit = state_limit
    def rlp(self, data_override=None):
        tgt = rlp_bytes(addr20(self.target)) if self.target is not None else rlp_bytes(b"")
        data = self.data if data_override is None else data_override
        limits = rlp_list([rlp_int(self.gas_limit), rlp_int(self.state_limit)])
        return rlp_list([rlp_int(self.mode), rlp_int(self.flags), tgt,
                         limits, rlp_int(self.value), rlp_bytes(data)])

class FrameSig:
    ARBITRARY = 0
    SECP256K1 = 1
    P256 = 2
    def __init__(self, scheme, signer, msg, signature):
        self.scheme, self.signer, self.msg, self.signature = scheme, signer, msg, signature
    def rlp(self, elide=False, signature_override=None):
        sig = self.signature if signature_override is None else signature_override
        sig = b"" if (elide and len(self.msg) == 0) else sig
        return rlp_list([rlp_int(self.scheme), rlp_bytes(addr20(self.signer)),
                         rlp_bytes(self.msg), rlp_bytes(sig)])

class FrameTx:
    def __init__(self, chain_id, nonce_keys, nonce_seq, sender, frames, signatures,
                 max_priority_fee, max_fee, max_blob_fee=0, blob_hashes=None, recent_root_refs=None):
        self.chain_id, self.nonce_keys, self.nonce_seq, self.sender = chain_id, nonce_keys, nonce_seq, sender
        self.frames, self.signatures = frames, signatures
        self.max_priority_fee, self.max_fee, self.max_blob_fee = max_priority_fee, max_fee, max_blob_fee
        self.blob_hashes = blob_hashes or []
        self.recent_root_refs = recent_root_refs or []
    def _envelope(self, elide_sigs, field_overrides=None):
        field_overrides = field_overrides or {}
        return [
            rlp_int(self.chain_id),
            rlp_list([rlp_int(k) for k in self.nonce_keys]),
            rlp_int(self.nonce_seq),
            rlp_bytes(addr20(self.sender)),
            rlp_list([f.rlp(field_overrides.get(("frame", i)))
                      for i, f in enumerate(self.frames)]),
            rlp_list([s.rlp(elide=elide_sigs,
                            signature_override=field_overrides.get(("signature", i)))
                      for i, s in enumerate(self.signatures)]),
            rlp_list([rlp_int(self.max_priority_fee),
                      rlp_int(self.max_fee),
                      rlp_int(self.max_blob_fee)]),
            rlp_list([rlp_bytes(h) for h in self.blob_hashes]),
            rlp_list([r for r in self.recent_root_refs]),  # entries pre-encoded if any
        ]
    def encode(self) -> bytes:
        return rlp_list(self._envelope(elide_sigs=False))
    def raw(self) -> bytes:
        return bytes([0x06]) + self.encode()
    def sig_hash(self) -> bytes:
        return keccak(bytes([0x06]) + rlp_list(self._envelope(elide_sigs=True)))

    @staticmethod
    def _calldata_gas(encoded: bytes) -> int:
        return sum(4 if b == 0 else 16 for b in encoded)

    @staticmethod
    def _floor_tokens(encoded: bytes) -> int:
        # EIP-8141 `floor_tokens_in`: the calldata floor prices every byte alike. Only the
        # standard charge above distinguishes zero bytes from nonzero ones.
        return 4 * len(encoded)

    def _nonce_calldata(self) -> bytes:
        return rlp_list([rlp_int(k) for k in self.nonce_keys]) + rlp_int(self.nonce_seq)

    def _named_data_fields(self):
        # EIP-8141 prices the data fields, not the surrounding frame/signature
        # RLP or scalar frame metadata.
        for i, frame in enumerate(self.frames):
            yield ("frame", i), frame.data
        for i, sig in enumerate(self.signatures):
            yield ("signature_signer", i), addr20(sig.signer)
            yield ("signature_message", i), sig.msg
            yield ("signature", i), sig.signature
        yield ("nonce", 0), self._nonce_calldata()
        if self.recent_root_refs:
            yield ("recent_roots", 0), rlp_list(self.recent_root_refs)

    def _data_fields(self):
        for name, data in self._named_data_fields():
            yield data

    def signature_verification_cost(self) -> int:
        costs = {
            FrameSig.ARBITRARY: 100,
            FrameSig.SECP256K1: 2_800,
            FrameSig.P256: 6_700,
        }
        return sum(costs[s.scheme] for s in self.signatures)

    def recent_root_reference_intrinsic_gas(self) -> int:
        # EIP-8272 defines these BY FORMULA over the EIP-8038 access-list constants, so they
        # follow the schedule rather than being fixed: on the v8.1.0 schedule this branch
        # carries, one address at 2400 plus 1900 + 2*30 + 7*6 = 2002 per referenced key.
        # The frozen file's 3000/3102 are the same formula on the older schedule.
        return 0 if not self.recent_root_refs else 2_400 + len(self.recent_root_refs) * 2_002

    def _moves_value(self, frame) -> bool:
        # EIP-8141 `value_cost`: a frame with no target, or one targeting the sender, moves
        # nothing whatever its `value`, so it is not charged.
        return (frame.value > 0 and frame.target is not None
                and addr20(frame.target) != addr20(self.sender))

    def value_transfer_cost(self) -> int:
        """`TX_VALUE_COST` per frame that moves value to another account (EIP-8141).

        Per frame, not per distinct recipient, and static because `value` and `target` are
        transaction fields. It covers the recipient balance write and the EIP-7708 transfer
        log, exactly as EIP-2780 prices a top-level transfer.
        """
        return 6_000 * sum(1 for f in self.frames if self._moves_value(f))

    def mandatory_gas(self) -> int:
        # The terms `frame_tx_intrinsic_gas` and `calldata_floor_gas` share. A cost that
        # belongs on both sides of the `max_gas` comparison must live here, or it is silently
        # dropped whenever the floor binds.
        return (12_000 + len(self.frames) * 475
                + self.signature_verification_cost()
                + self.value_transfer_cost()
                + self.recent_root_reference_intrinsic_gas())

    def state_gas_limit(self) -> int:
        return sum(f.state_limit for f in self.frames)

    def standard_gas_limit(self) -> int:
        data_cost = sum(self._calldata_gas(field) for field in self._data_fields())
        return (self.mandatory_gas() + data_cost
                + sum(f.gas_limit for f in self.frames)
                + self.state_gas_limit())

    def calldata_floor_gas(self) -> int:
        tokens = sum(self._floor_tokens(field) for field in self._data_fields())
        return self.mandatory_gas() + 16 * tokens

    def total_gas_limit(self) -> int:
        """EIP-8141 `max_gas = max(standard_gas_limit, calldata_floor_gas + sum(limits.state))`.

        State gas is added on top of the floor rather than absorbed by it: the floor bounds
        what a transaction's data costs to include, and state growth never rides free under
        it.
        """
        return max(self.standard_gas_limit(),
                   self.calldata_floor_gas() + self.state_gas_limit())

    def validation_prefix(self):
        """Return the validation prefix for an accepted MATCHA v1 shape.

        MATCHA charges declared gas only for frames that public-mempool
        admission executes. Refuse unknown shapes instead of accidentally
        undercharging an extension.
        """
        offset = 0
        if (self.frames and self.frames[0].mode == 1
                and self.frames[0].flags == 0
                and self.frames[0].target == 0x8141
                and len(self.frames[0].data) == 8):
            offset = 1
        flags = [frame.flags for frame in self.frames[offset:offset + 2]
                 if frame.mode == 1]
        if flags[:1] == [0x03]:
            end = offset + 1
        elif flags == [0x02, 0x01]:
            end = offset + 2
        else:
            raise ValueError("unknown MATCHA validation-prefix shape")
        if any(frame.mode == 1 for frame in self.frames[end:]):
            raise ValueError("VERIFY frame after MATCHA validation prefix")
        return self.frames[:end]

    def public_validation_gas(self) -> int:
        """EIP-8141 MAX_VERIFY_GAS quantity for the accepted prefix."""
        return (self.signature_verification_cost()
                + sum(frame.gas_limit for frame in self.validation_prefix()))

    def admission_gas(self) -> int:
        """Exact-byte MATCHA admission gas for the current fork profile."""
        if self.blob_hashes:
            raise ValueError("MATCHA version one rejects blob transactions")
        fields = list(self._data_fields())
        standard = (self.mandatory_gas()
                    + sum(self._calldata_gas(field) for field in fields)
                    + sum(frame.gas_limit for frame in self.validation_prefix()))
        floor = (self.mandatory_gas()
                 + 16 * sum(self._floor_tokens(field) for field in fields))
        return max(standard, floor)

    def matcha_charge(self, safety_factor=2) -> int:
        if not isinstance(safety_factor, int) or safety_factor <= 0:
            raise ValueError("MATCHA safety factor must be a positive integer")
        return safety_factor * self.admission_gas()

    def matcha_min_priority_fee(self, live_other, p0, safety_factor=2) -> int:
        """Minimum integer priority fee satisfying q*tip >= p0*(L_other+q)."""
        if live_other < 0 or p0 <= 0:
            raise ValueError("MATCHA requires live_other >= 0 and p0 > 0")
        q = self.matcha_charge(safety_factor)
        numerator = p0 * (live_other + q)
        return (numerator + q - 1) // q

    def effective_priority_fee(self, base_fee) -> int:
        """EIP-1559 priority-fee headroom at the quoted base fee."""
        if base_fee < 0:
            raise ValueError("base fee must be nonnegative")
        return min(self.max_priority_fee, max(0, self.max_fee - base_fee))

    def matcha_price_ok(self, live_other, p0, base_fee, safety_factor=2) -> bool:
        return self.effective_priority_fee(base_fee) >= self.matcha_min_priority_fee(
            live_other, p0, safety_factor
        )

    def max_cost(self, blob_base_fee=0) -> int:
        return (self.max_fee * self.total_gas_limit()
                + len(self.blob_hashes) * 131_072 * blob_base_fee)

# ---------- golden-vector validation ----------
if __name__ == "__main__":
    # This envelope must differ from the frozen encoding of the same transaction in exactly two
    # places — the nested fees and the per-frame limits list — and the sig hash must move
    # with it. Rather than pin a hand-computed vector, which only proves this file agrees
    # with itself, assert the structural invariants and the divergence from the frozen dialect.
    # The authoritative check is `test_frametx_against_node.py`, which offers the bytes
    # to a live ethrex.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "frametx_frozen",
        pathlib.Path(__file__).resolve().parent.parent / "devnet/vectors/2026-09-01-hegota-final-profile/frametx.py",
    )
    frozen = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(frozen)

    def build(mod, **kw):
        return mod.FrameTx(
            chain_id=1,
            nonce_keys=[0],
            nonce_seq=7,
            sender=0xABCD,
            frames=[
                mod.Frame(mode=1, flags=3, target=None, gas_limit=0x5208, value=0,
                          data=bytes([0x11, 0x22]), **kw),
                mod.Frame(mode=2, flags=0, target=0x1234, gas_limit=0x9C40, value=0, data=b""),
            ],
            signatures=[mod.FrameSig(mod.FrameSig.SECP256K1, 0xABCD, b"", bytes([0x01] * 65))],
            max_priority_fee=0x3B9ACA00,
            max_fee=0x6FC23AC00,
        )

    spec_tx = build(sys.modules[__name__])
    frozen_tx = build(frozen)

    ok = True

    def check(name, cond, detail=""):
        global ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
        ok = ok and cond

    # 9 top-level fields, not 11: the three fee scalars became one list.
    body = spec_tx.encode()
    fields = rlp_items(body)
    check("the envelope has 9 top-level fields", len(fields) == 9, f"got {len(fields)}")
    check("field 6 is the nested fees list", len(rlp_items(fields[6])) == 3,
          f"got {len(rlp_items(fields[6]))} entries")
    # Each frame carries [execution, state] where the frozen dialect carried one scalar.
    frame0 = rlp_items(rlp_items(fields[4])[0])
    check("a frame has 6 fields", len(frame0) == 6, f"got {len(frame0)}")
    check("its limits field is a 2-element list", len(rlp_items(frame0[3])) == 2,
          f"got {len(rlp_items(frame0[3]))}")

    check("the spec encoding differs from the frozen one", spec_tx.encode() != frozen_tx.encode())
    check("the sig hash differs from the frozen one", spec_tx.sig_hash() != frozen_tx.sig_hash())

    # The intrinsic dropped 3000 and neither frame carries value, so max_gas is exactly
    # 3000 below the frozen dialect's for the same transaction. This pins the constant.
    check("max_gas is 3000 below the frozen dialect (intrinsic 15000 -> 12000)",
          frozen_tx.total_gas_limit() - spec_tx.total_gas_limit() == 3_000,
          f"frozen {frozen_tx.total_gas_limit()} vs spec {spec_tx.total_gas_limit()}")

    # A declared state budget must reach max_gas; the frozen dialect has nowhere to put it.
    stateful = build(sys.modules[__name__], state_limit=97_920)
    check("a frame's limits.state raises max_gas by exactly that amount",
          stateful.total_gas_limit() - spec_tx.total_gas_limit() == 97_920,
          f"delta {stateful.total_gas_limit() - spec_tx.total_gas_limit()}")

    # TX_VALUE_COST is per frame that moves value to another account: frame 1 targets 0x1234
    # from sender 0xABCD.
    valued = build(sys.modules[__name__])
    valued.frames[1].value = 1
    check("a frame moving value to another account adds TX_VALUE_COST",
          valued.total_gas_limit() - spec_tx.total_gas_limit() == 6_000,
          f"delta {valued.total_gas_limit() - spec_tx.total_gas_limit()}")
    # ...and only then: a targetless frame and a self-targeted frame move nothing.
    targetless = build(sys.modules[__name__])
    targetless.frames[0].value = 1
    check("value on a targetless frame adds nothing",
          targetless.mandatory_gas() == spec_tx.mandatory_gas(),
          f"delta {targetless.mandatory_gas() - spec_tx.mandatory_gas()}")
    self_pay = build(sys.modules[__name__])
    self_pay.frames[1].target, self_pay.frames[1].value = self_pay.sender, 1
    check("value to the sender itself adds nothing",
          self_pay.mandatory_gas() == spec_tx.mandatory_gas(),
          f"delta {self_pay.mandatory_gas() - spec_tx.mandatory_gas()}")

    # The floor prices zero and nonzero bytes alike; only the standard charge weights them.
    zeros = build(sys.modules[__name__])
    zeros.frames[0].data = bytes(64)
    ones = build(sys.modules[__name__])
    ones.frames[0].data = bytes([0x01]) * 64
    check("the calldata floor is the same for 64 zero bytes and 64 nonzero bytes",
          zeros.calldata_floor_gas() == ones.calldata_floor_gas(),
          f"{zeros.calldata_floor_gas()} vs {ones.calldata_floor_gas()}")
    check("the standard charge still weights them 4 vs 16 per byte",
          ones.standard_gas_limit() - zeros.standard_gas_limit() == 64 * 12,
          f"delta {ones.standard_gas_limit() - zeros.standard_gas_limit()}")

    sys.exit(0 if ok else 1)
