#!/usr/bin/env python3
"""Ethrex v23 Hegotá FrameTx (type 0x06) encoder.

This is the exact EIP-8141/8250/8272 testnet dialect deployed on chain 8141.
It is not the newer EIP-8141 draft wire format, which uses nested fees and
separate execution and state gas limits.

Wire layout (ethrex hegota-devnet), verified against the repo golden vector:
  raw = 0x06 || rlp([chain_id, nonce_keys, nonce_seq, sender, frames, signatures,
                     max_priority_fee, max_fee, max_blob_fee, blob_hashes,
                     recent_root_references])
  frame     = rlp([mode, flags, target_or_empty, gas_limit, value, data])
  signature = rlp([scheme, signer, msg, signature_bytes])  # 0=ARBITRARY, 1=SECP256K1, 2=P256
  sig_hash  = keccak256(0x06 || rlp(envelope with empty-msg signatures' bytes elided))
"""
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

# ---------- frame-tx model ----------
class Frame:
    def __init__(self, mode, flags, target, gas_limit, value, data):
        self.mode, self.flags, self.target = mode, flags, target  # target: 20-byte int/bytes or None
        self.gas_limit, self.value, self.data = gas_limit, value, data
    def rlp(self, data_override=None):
        tgt = rlp_bytes(addr20(self.target)) if self.target is not None else rlp_bytes(b"")
        data = self.data if data_override is None else data_override
        return rlp_list([rlp_int(self.mode), rlp_int(self.flags), tgt,
                         rlp_int(self.gas_limit), rlp_int(self.value), rlp_bytes(data)])

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
            rlp_int(self.max_priority_fee),
            rlp_int(self.max_fee),
            rlp_int(self.max_blob_fee),
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
    def _calldata_tokens(encoded: bytes) -> int:
        return sum(1 if b == 0 else 4 for b in encoded)

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
        # EIP-8272 resolves the EIP-8038 access-list constants at Hegota:
        # one address at 3000 plus 3102 per referenced storage key.
        return 0 if not self.recent_root_refs else 3_000 + len(self.recent_root_refs) * 3_102

    def mandatory_gas(self) -> int:
        return (15_000 + len(self.frames) * 475
                + self.signature_verification_cost()
                + self.recent_root_reference_intrinsic_gas())

    def standard_gas_limit(self) -> int:
        data_cost = sum(self._calldata_gas(field) for field in self._data_fields())
        return self.mandatory_gas() + data_cost + sum(f.gas_limit for f in self.frames)

    def calldata_floor_gas(self) -> int:
        tokens = sum(self._calldata_tokens(field) for field in self._data_fields())
        return self.mandatory_gas() + 16 * tokens

    def total_gas_limit(self) -> int:
        """EIP-8141 max_gas, matching deployed ethrex a2302ead."""
        return max(self.standard_gas_limit(), self.calldata_floor_gas())

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
                 + 16 * sum(self._calldata_tokens(field) for field in fields))
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
    golden = FrameTx(
        chain_id=1,
        nonce_keys=[0],
        nonce_seq=7,
        sender=0xABCD,
        frames=[
            Frame(mode=1, flags=3, target=None, gas_limit=0x5208, value=0, data=bytes([0x11, 0x22])),
            Frame(mode=2, flags=0, target=0x1234, gas_limit=0x9c40, value=0, data=b""),
        ],
        signatures=[FrameSig(FrameSig.SECP256K1, 0xABCD, b"", bytes([0x01] * 65))],
        max_priority_fee=0x3b9aca00,
        max_fee=0x6fc23ac00,
    )
    EXPECT_RLP = "f8ae01c1800794000000000000000000000000000000000000abcde8ca01038082520880821122dc0280940000000000000000000000000000000000001234829c408080f85cf85a0194000000000000000000000000000000000000abcd80b8410101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101843b9aca008506fc23ac0080c0c0"
    EXPECT_SIGHASH = "0x989e6ce4dc87b2afd5cfa6c780ff60f01fc3b40c77057cf872410145d69f715c"
    EXPECT_TOTAL_GAS = 80_974
    got_rlp = golden.encode().hex()
    got_sh = "0x" + golden.sig_hash().hex()
    print("RLP match:     ", got_rlp == EXPECT_RLP)
    if got_rlp != EXPECT_RLP:
        print("  expected:", EXPECT_RLP)
        print("  got:     ", got_rlp)
    print("sig_hash match:", got_sh == EXPECT_SIGHASH)
    if got_sh != EXPECT_SIGHASH:
        print("  expected:", EXPECT_SIGHASH)
        print("  got:     ", got_sh)
    got_gas = golden.total_gas_limit()
    print("total gas match:", got_gas == EXPECT_TOTAL_GAS)
    if got_gas != EXPECT_TOTAL_GAS:
        print("  expected:", EXPECT_TOTAL_GAS)
        print("  got:     ", got_gas)
    sys.exit(0 if got_rlp == EXPECT_RLP and got_sh == EXPECT_SIGHASH
             and got_gas == EXPECT_TOTAL_GAS else 1)
