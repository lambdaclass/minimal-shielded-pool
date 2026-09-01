#!/usr/bin/env python3
"""Offer `frametx.py`'s bytes to a live ethrex and let the node judge them.

An encoder that only agrees with itself proves nothing: the failure mode that matters is a
wire format the node reads differently, and only the node can rule that out.
`ethrex_simulateFrameTransaction` decodes canonically before judging anything, so a business
rule reported back means the envelope parsed — a `BadParams` means these bytes are wrong.

The frozen encoder's output is offered too, as the control. On a spec chain it must be rejected;
if both encodings are accepted, the node is not enforcing the format either encoder targets
and neither result means anything.

Usage: test_frametx_against_node.py <rpc_url>
"""
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import frametx as spec  # noqa: E402

# The frozen control comes from the archived record, not from a live module: the whole
# point of the check is that a pre-relaunch envelope must be rejected by a spec chain.
import importlib.util  # noqa: E402

_arch = (pathlib.Path(__file__).resolve().parent
         / "vectors/2026-09-01-hegota-final-profile/frametx.py")
_frozen_spec = importlib.util.spec_from_file_location("frametx_frozen", _arch)
frozen = importlib.util.module_from_spec(_frozen_spec)
_frozen_spec.loader.exec_module(frozen)

RPC = sys.argv[1]
FAILURES = []


def rpc(method, params):
    req = urllib.request.Request(
        RPC,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def build(mod, chain_id, sender, **frame_kw):
    """A self-verify shape: one VERIFY frame approving execution and payment."""
    return mod.FrameTx(
        chain_id=chain_id,
        nonce_keys=[0],
        nonce_seq=0,
        sender=sender,
        frames=[
            mod.Frame(mode=1, flags=3, target=sender, gas_limit=80_000, value=0, data=b"",
                      **frame_kw),
            mod.Frame(mode=2, flags=0, target=0xC0DE, gas_limit=30_000, value=0, data=b""),
        ],
        signatures=[mod.FrameSig(mod.FrameSig.SECP256K1, sender, b"", bytes([0x01] * 65))],
        max_priority_fee=10**9,
        max_fee=10**10,
    )


def main() -> int:
    chain_id = int(rpc("eth_chainId", [])["result"], 16)
    sender = 0xD277B144F4C62839EF04BD4282D1D852D4A956E3
    print(f"node chain={chain_id}")

    spec_raw = "0x06" + build(spec, chain_id, sender, state_limit=0).encode().hex()
    out = rpc("ethrex_simulateFrameTransaction", [spec_raw])
    # A business-rule verdict (valid true/false) means the bytes decoded. A BadParams-style
    # error means they did not.
    decoded = "result" in out
    check("the node decodes the spec envelope", decoded,
          json.dumps(out.get("error", out.get("result")))[:120])

    frozen_raw = "0x06" + build(frozen, chain_id, sender).encode().hex()
    out_frozen = rpc("ethrex_simulateFrameTransaction", [frozen_raw])
    check("the node REJECTS the frozen envelope on a spec chain", "result" not in out_frozen,
          json.dumps(out_frozen.get("error", out_frozen.get("result")))[:120])

    # The state dimension must survive the round trip: a declared budget changes the bytes,
    # and the node must still decode them.
    stateful = "0x06" + build(spec, chain_id, sender, state_limit=97_920).encode().hex()
    out_state = rpc("ethrex_simulateFrameTransaction", [stateful])
    check("the node decodes a frame that declares limits.state", "result" in out_state,
          json.dumps(out_state.get("error", out_state.get("result")))[:120])
    check("declaring state gas changes the encoding", stateful != spec_raw)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("every check passed")
    return 0


sys.exit(main())
