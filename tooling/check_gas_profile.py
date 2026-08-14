#!/usr/bin/env python3
"""Check the immutable Hegota testnet gas profile against recorded evidence.

The settlement bound deliberately overcounts the active EIP-8037/8038 storage
costs: it starts from a measured pre-8037 execution that already paid legacy
SSTORE gas, then adds the full current-fork regular charge for every reachable
SSTORE and the state-growth charge for every new slot. This makes the result a
conservative upper bound rather than a gas estimate.
"""
import json


VERIFY_FRAME_GAS = 320_000
MAX_OBSERVED_VERIFY_GAS = 294_401

SETTLE_FRAME_GAS = 2_000_000
LOCAL_WORST_SETTLEMENT_GAS = 832_626

# Rollover + two outputs + a first withdrawal credit performs at most 33
# SSTORE operations. Five end in previously absent slots: finalized root,
# epoch counter, two leaf markers, and the withdrawal credit.
MAX_SSTORE_OPERATIONS = 33
MAX_NEW_STORAGE_SLOTS = 5

# EIP-8038: cold access (2,100) + STORAGE_WRITE (10,000).
EIP_8038_COLD_WRITE_GAS = 12_100
# EIP-8037: 64 state bytes * 1,530 gas per state byte.
EIP_8037_NEW_SLOT_STATE_GAS = 64 * 1_530

CONSERVATIVE_SETTLEMENT_BOUND = (
    LOCAL_WORST_SETTLEMENT_GAS
    + MAX_SSTORE_OPERATIONS * EIP_8038_COLD_WRITE_GAS
    + MAX_NEW_STORAGE_SLOTS * EIP_8037_NEW_SLOT_STATE_GAS
)


def main():
    assert MAX_OBSERVED_VERIFY_GAS < VERIFY_FRAME_GAS
    assert CONSERVATIVE_SETTLEMENT_BOUND < SETTLE_FRAME_GAS
    print(json.dumps({
        "verify": {
            "cap": VERIFY_FRAME_GAS,
            "max_observed": MAX_OBSERVED_VERIFY_GAS,
            "margin": VERIFY_FRAME_GAS - MAX_OBSERVED_VERIFY_GAS,
        },
        "settlement": {
            "cap": SETTLE_FRAME_GAS,
            "local_worst": LOCAL_WORST_SETTLEMENT_GAS,
            "conservative_eip_8037_8038_bound": CONSERVATIVE_SETTLEMENT_BOUND,
            "margin": SETTLE_FRAME_GAS - CONSERVATIVE_SETTLEMENT_BOUND,
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
