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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "devnet"))

from gas_profile import (  # noqa: E402
    HEGOTA_TESTNET_MAX_VERIFY_GAS,
    KEYED_NONCE_FIRST_USE_STATE_GAS,
    MAX_VERIFY_STATE_GAS,
    RECENT_ROOT_FRAME_GAS,
    REQUIRED_VERIFY_BUDGET,
    SETTLE_FRAME_GAS,
    SETTLE_FRAME_STATE_GAS,
    SPEND_NONCE_KEY_COUNT,
    VERIFY_FRAME_GAS,
    VERIFY_FRAME_STATE_GAS,
)

PRE_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS = 294_401
PRE_PR_12279_KEYED_NONCE_EXECUTION_GAS = 2 * 20_000
# Measured on a devnet running EIP-8250 at f3079a09e8 and EIP-8272 at 824cbc0b0e:
# the transfer's proof frame reported 254,685 and the withdraw's 254,712. The drop
# from the pre-12279 figure is the keyed-nonce first use leaving the execution
# dimension for the state one, which is the whole point of that PR.
POST_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS = 254_712
# The pool grammar permits exactly one 72-byte recent-root tuple, pinned by the
# dispatcher's `frameParam(0, 0x04) == 72`. The measured verifier-frame execution cost
# for that shape was 5,579 gas, so the pinned 30,000-gas limit covers it.
#
# This figure says nothing about a frame carrying the sixteen tuples EIP-8272 allows.
# An earlier revision claimed it did, on a measurement that repeated ONE tuple sixteen
# times: identical (source_id, slot) pairs share a storage key, so that run paid one
# cold SLOAD and fifteen warm ones. Sixteen distinct roots are sixteen cold SLOADs,
# 33,600 gas before the rest of the verifier runs, which does not fit 30,000 at all.
MAX_OBSERVED_RECENT_ROOT_FRAME_GAS = 5_579
CONSERVATIVE_VERIFY_STATE_BOUND = (
    SPEND_NONCE_KEY_COUNT * KEYED_NONCE_FIRST_USE_STATE_GAS
)

# Rollover + two outputs + a first withdrawal credit performs at most 33 SSTORE operations.
# Five end in previously absent slots: finalized root, epoch counter, two leaf markers, and
# the withdrawal credit. Only those five grow the state.
MAX_SSTORE_OPERATIONS = 33
MAX_NEW_STORAGE_SLOTS = 5

LOCAL_WORST_SETTLEMENT_GAS = 832_626
# EIP-8038: cold access (2,100) + STORAGE_WRITE (10,000).
EIP_8038_COLD_WRITE_GAS = 12_100
# EIP-8037 uses the same state gas for any new storage slot.
EIP_8037_NEW_SLOT_STATE_GAS = KEYED_NONCE_FIRST_USE_STATE_GAS

# The execution dimension no longer carries state growth.
CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND = (
    LOCAL_WORST_SETTLEMENT_GAS + MAX_SSTORE_OPERATIONS * EIP_8038_COLD_WRITE_GAS
)
CONSERVATIVE_SETTLEMENT_STATE_BOUND = MAX_NEW_STORAGE_SLOTS * EIP_8037_NEW_SLOT_STATE_GAS

# What the frozen profile had to declare for the same work, as one number.
FROZEN_SETTLE_FRAME_GAS = 2_000_000


def main():
    # This is a conservative comparison only. The measurement came from a node
    # that charged 40,000 keyed nonce gas as execution. No PR 12279 node exists
    # yet, so there is no post-change execution measurement.
    assert PRE_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS < VERIFY_FRAME_GAS
    # The measured figures must fit the budgets the dispatcher pins, or a spend that
    # simulates fine halts mid-frame on a chain with slightly different access costs.
    assert POST_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS < VERIFY_FRAME_GAS
    assert MAX_OBSERVED_RECENT_ROOT_FRAME_GAS < RECENT_ROOT_FRAME_GAS
    assert CONSERVATIVE_SETTLEMENT_EXECUTION_BOUND < SETTLE_FRAME_GAS
    assert CONSERVATIVE_SETTLEMENT_STATE_BOUND < SETTLE_FRAME_STATE_GAS
    # The proof itself writes nothing. This exact budget exists only because its
    # payment APPROVE creates the two EIP-8250 keyed-nonce slots.
    assert VERIFY_FRAME_STATE_GAS == CONSERVATIVE_VERIFY_STATE_BOUND
    # EIP-8272: the recent-root verifier frame joins the public mempool's verify budget
    # and the prefix's state budgets stay under EIP-8141's MAX_VERIFY_STATE_GAS. One
    # tuple costs the predeploy's cold entry plus two keccaks and a cold SLOAD, well
    # under the pinned frame budget.
    assert REQUIRED_VERIFY_BUDGET == RECENT_ROOT_FRAME_GAS + VERIFY_FRAME_GAS + 2_800
    assert REQUIRED_VERIFY_BUDGET <= HEGOTA_TESTNET_MAX_VERIFY_GAS
    assert VERIFY_FRAME_STATE_GAS <= MAX_VERIFY_STATE_GAS

    declared_split = (VERIFY_FRAME_GAS + VERIFY_FRAME_STATE_GAS
                   + SETTLE_FRAME_GAS + SETTLE_FRAME_STATE_GAS)
    declared_single = VERIFY_FRAME_GAS + FROZEN_SETTLE_FRAME_GAS
    pre_pr_12279_split = declared_split - CONSERVATIVE_VERIFY_STATE_BOUND
    pre_pr_12279_saving = declared_single - pre_pr_12279_split
    extra_over_frozen = declared_split - declared_single
    assert pre_pr_12279_saving == 50_000
    assert extra_over_frozen == 145_840
    assert extra_over_frozen == CONSERVATIVE_VERIFY_STATE_BOUND - pre_pr_12279_saving

    # The dispatcher must enforce the same five limits the wallet emits. Yul
    # cannot import the Python module, so check its unavoidable literals here.
    dispatcher = (ROOT / "devnet" / "ShieldedPoolDispatcher.yul").read_text()
    dispatcher_pins = (
        f"if iszero(eq(frameParam(0, 0x01), {RECENT_ROOT_FRAME_GAS})) {{ fail(errShape()) }}",
        f"if iszero(eq(frameParam(1, 0x01), {VERIFY_FRAME_GAS})) {{ fail(errShape()) }}",
        f"if iszero(eq(frameParam(1, 0x09), {VERIFY_FRAME_STATE_GAS})) {{ fail(errShape()) }}",
        f"if iszero(eq(frameParam(2, 0x01), {SETTLE_FRAME_GAS})) {{ fail(errShape()) }}",
        f"if iszero(eq(frameParam(2, 0x09), {SETTLE_FRAME_STATE_GAS})) {{ fail(errShape()) }}",
    )
    assert all(pin in dispatcher for pin in dispatcher_pins), \
        "dispatcher gas limits differ from devnet/gas_profile.py"

    # Keep the checked-in deployment record aligned as well. The live runner
    # rewrites these fields from the activation manifest after deployment.
    cfg = json.loads((ROOT / "devnet" / "deploy_config.json").read_text())
    assert cfg["profile"] == "eip8272-canonical-frame"
    assert cfg["recentRootGas"] == RECENT_ROOT_FRAME_GAS
    assert cfg["verifyGas"] == VERIFY_FRAME_GAS
    assert cfg["verifyStateGas"] == VERIFY_FRAME_STATE_GAS
    assert cfg["settleGas"] == SETTLE_FRAME_GAS
    assert cfg["settleStateGas"] == SETTLE_FRAME_STATE_GAS

    print(json.dumps({
        "verify": {
            "execution_cap": VERIFY_FRAME_GAS,
            "state_cap": VERIFY_FRAME_STATE_GAS,
            "pre_pr_12279_observed_execution": PRE_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS,
            "pre_pr_12279_keyed_nonce_execution_gas": PRE_PR_12279_KEYED_NONCE_EXECUTION_GAS,
            "post_pr_12279_observed_execution": POST_PR_12279_MAX_OBSERVED_VERIFY_EXECUTION_GAS,
            "keyed_nonce_state_bound": CONSERVATIVE_VERIFY_STATE_BOUND,
        },
        "recent_root": {
            "execution_cap": RECENT_ROOT_FRAME_GAS,
            "observed_execution_one_tuple": MAX_OBSERVED_RECENT_ROOT_FRAME_GAS,
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
            "pre_pr_12279_two_dimensions": pre_pr_12279_split,
            "spec_two_dimensions": declared_split,
            "pr_12279_state_gas_increase": CONSERVATIVE_VERIFY_STATE_BOUND,
            "extra_over_frozen": extra_over_frozen,
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
