#!/usr/bin/env python3
"""The EIP-8272 recent-root window, checked at both boundaries.

The wallet refuses a publication slot the node would refuse, and the two must agree
exactly. Admission judges a transaction against the earliest block that could carry
it, so the node's `current_slot` is the head slot plus one. A wallet that compares
against the head slot instead is one slot too generous at the old end: it signs a
transaction whose protocol age the node computes as one higher, and which the node
then rejects. That is a silent failure, because the wallet has already spent the
proof and the operator sees only a mempool refusal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gas_profile import RECENT_ROOT_TUPLE_BYTES
from pool_frametx import RECENT_ROOT_LENGTH, recent_root_window_error

# Mirrors ethrex's FRAME_TX_RECENT_ROOT_USABLE_WINDOW. The node rejects when
# `current_slot - slot` exceeds it.
USABLE_WINDOW = RECENT_ROOT_LENGTH - 1

HEAD = 100_000


def node_rejects(slot, latest_slot=HEAD):
    """What ethrex's `check_recent_root_frame_at_root` decides, restated here."""
    current_slot = latest_slot + 1
    return slot >= current_slot or current_slot - slot > USABLE_WINDOW


def main():
    assert RECENT_ROOT_LENGTH == 8192
    assert USABLE_WINDOW == 8191
    assert RECENT_ROOT_TUPLE_BYTES == 72

    # Ages are measured from the head slot, which is what an operator reads off the
    # chain. The node adds one to get the slot it admits against.
    cases = {
        "age 8190, inside the window": HEAD - 8190,
        "age 8191, the boundary that used to pass here and fail on chain": HEAD - 8191,
        "age 8192, outside the window": HEAD - 8192,
        "published in the head slot, usable from the next one": HEAD,
        "published one slot ahead of the head": HEAD + 1,
        "age 1, freshly published": HEAD - 1,
    }

    disagreements = []
    for label, slot in cases.items():
        wallet = recent_root_window_error(slot, HEAD) is not None
        node = node_rejects(slot)
        if wallet != node:
            disagreements.append(
                f"{label}: wallet {'rejects' if wallet else 'accepts'}, "
                f"node {'rejects' if node else 'accepts'}")
    if disagreements:
        raise SystemExit("wallet and node disagree:\n  " + "\n  ".join(disagreements))

    # The two boundaries, stated as outcomes rather than as agreement, so the test
    # still means something if both sides are changed together by mistake.
    assert recent_root_window_error(HEAD - 8190, HEAD) is None, "age 8190 must pass"
    assert recent_root_window_error(HEAD - 8191, HEAD) is not None, "age 8191 must fail"
    assert recent_root_window_error(HEAD - 8192, HEAD) is not None, "age 8192 must fail"
    assert recent_root_window_error(HEAD, HEAD) is None, "head-slot publication is usable next slot"
    assert recent_root_window_error(HEAD + 1, HEAD) is not None, "a future slot must fail"

    # The whole window, exhaustively, against the restated node rule.
    for age in range(0, USABLE_WINDOW + 3):
        slot = HEAD - age
        assert (recent_root_window_error(slot, HEAD) is not None) == node_rejects(slot), \
            f"disagreement at age {age}"

    print({"window": RECENT_ROOT_LENGTH, "usable": USABLE_WINDOW,
           "oldest_accepted_age_from_head": USABLE_WINDOW - 1,
           "boundaries_checked": USABLE_WINDOW + 3})


main()
