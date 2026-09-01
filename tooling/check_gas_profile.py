#!/usr/bin/env python3
"""Check the EIP-8141 the current spec gas profile: two declared dimensions per frame.

the frozen profile derives the execution/state split at runtime, so a frame declares one number and the
state charges spill into it. the current spec makes the split explicit — each frame declares
`limits = [execution, state]`, and the two pools never lend to each other. For this pool
that is not merely a re-encoding, it changes what has to be bounded:

  * the state growth the settlement performs leaves the execution budget, so the execution
    cap can drop by exactly the state-growth charge it used to have to cover;
  * the state budget becomes a separate declared number the dispatcher must pin, because on
    the current spec `max_gas` includes the declared state budgets and the pool is the payer. An unpinned
    `limits.state` is an unbounded charge against the pool.

The bounds keep the frozen file's method: start from a measured worst-case execution, then add
the full current-fork charge for every reachable SSTORE, and account state growth separately
rather than folding it in.
"""
import json


VERIFY_FRAME_GAS = 320_000
VERIFY_FRAME_STATE_GAS = 0
MAX_OBSERVED_VERIFY_GAS = 294_401

# Rollover + two outputs + a first withdrawal credit performs at most 33 SSTORE operations.
# Five end in previously absent slots: finalized root, epoch counter, two leaf markers, and
# the withdrawal credit. Only those five grow the state.
MAX_SSTORE_OPERATIONS = 33
MAX_NEW_STORAGE_SLOTS = 5

LOCAL_WORST_SETTLEMENT_GAS = 832_626
# EIP-8038: cold access (2,100) + STORAGE_WRITE (10,000).
EIP_8038_COLD_WRITE_GAS = 12_100
# EIP-8037: 64 state bytes * 1,530 gas per state byte.
EIP_8037_NEW_SLOT_STATE_GAS = 64 * 1_530

# The execution dimension no longer carries state growth.
CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND = (
    LOCAL_WORST_SETTLEMENT_GAS + MAX_SSTORE_OPERATIONS * EIP_8038_COLD_WRITE_GAS
)
CONSERVATIVE_SETTLEMENT_STATE_BOUND = MAX_NEW_STORAGE_SLOTS * EIP_8037_NEW_SLOT_STATE_GAS

SETTLE_FRAME_GAS = 1_400_000
SETTLE_FRAME_STATE_GAS = 550_000

# What the frozen profile had to declare for the same work, as one number.
FROZEN_SETTLE_FRAME_GAS = 2_000_000


def main():
    assert MAX_OBSERVED_VERIFY_GAS < VERIFY_FRAME_GAS
    assert CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND < SETTLE_FRAME_GAS
    assert CONSERVATIVE_SETTLEMENT_STATE_BOUND < SETTLE_FRAME_STATE_GAS
    # The verify frame runs a proof and writes nothing, so declaring any state budget for it
    # would be paying for headroom it cannot use — on the current spec that is a real cost, since declared
    # state enters `max_gas` and the pool is the payer.
    assert VERIFY_FRAME_STATE_GAS == 0

    declared_split = (VERIFY_FRAME_GAS + VERIFY_FRAME_STATE_GAS
                   + SETTLE_FRAME_GAS + SETTLE_FRAME_STATE_GAS)
    declared_single = VERIFY_FRAME_GAS + FROZEN_SETTLE_FRAME_GAS
    assert declared_split < declared_single, "the split should not cost more than the single budget"

    print(json.dumps({
        "verify": {
            "execution_cap": VERIFY_FRAME_GAS,
            "state_cap": VERIFY_FRAME_STATE_GAS,
            "max_observed_execution": MAX_OBSERVED_VERIFY_GAS,
            "execution_margin": VERIFY_FRAME_GAS - MAX_OBSERVED_VERIFY_GAS,
        },
        "settlement": {
            "execution_cap": SETTLE_FRAME_GAS,
            "state_cap": SETTLE_FRAME_STATE_GAS,
            "conservative_execution_bound": CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND,
            "conservative_state_bound": CONSERVATIVE_SETTLEMENT_STATE_BOUND,
            "execution_margin": SETTLE_FRAME_GAS - CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND,
            "state_margin": SETTLE_FRAME_STATE_GAS - CONSERVATIVE_SETTLEMENT_STATE_BOUND,
        },
        "declared_total": {
            "frozen_single_dimension": declared_single,
            "spec_two_dimensions": declared_split,
            "reduction": declared_single - declared_split,
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
