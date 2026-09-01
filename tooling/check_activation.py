#!/usr/bin/env python3
"""Fail-closed artifact, ceremony, and gas-profile activation gate."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The settlement budgets each supported wire profile is allowed to declare, keyed by
# the manifest's `wire_profile`. An unlisted profile is rejected: the gate stays
# fail-closed, and adding a dialect means adding its budgets here deliberately rather
# than letting a manifest name its own.
#
# The current spec splits the single budget in two. The execution figure drops because the state
# growth moved out of it, not because settlement got cheaper, so a spec-profile manifest that
# still declared 2_000_000 would be over-provisioning the execution dimension by the
# whole state cost while declaring nothing for state — the shape that makes a frame
# halt for want of state gas with execution gas to spare.
PROFILES = {
    "ethrex-v23-hegota-testnet": {"settle_frame_gas": 2_000_000, "settle_frame_state_gas": None},
    "ethrex-v23-spec-2026-08-31": {"settle_frame_gas": 1_400_000, "settle_frame_state_gas": 550_000},
}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-testbed", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())

    for rel, expected in manifest["artifacts"].items():
        actual = sha256(ROOT / rel)
        if actual != expected:
            raise SystemExit(f"artifact hash mismatch: {rel}\nexpected {expected}\nactual   {actual}")

    profile = manifest["profile"]
    required = profile["verify_frame_gas"] + profile["signature_gas"]
    if required != profile["required_verify_budget"]:
        raise SystemExit("required verify budget is inconsistent")
    expected = PROFILES.get(profile["wire_profile"])
    if expected is None:
        raise SystemExit(f"unsupported transaction wire profile: {profile['wire_profile']!r}")
    if required > profile["hegota_profile_2_budget"]:
        raise SystemExit("transaction exceeds the configured Hegota Profile 2 budget")
    if profile["max_observed_verify_gas"] >= profile["verify_frame_gas"]:
        raise SystemExit("VERIFY frame does not cover the observed valid path")
    if profile["settle_frame_gas"] != expected["settle_frame_gas"]:
        raise SystemExit("settlement gas does not match the immutable dispatcher profile")
    if profile["conservative_settle_bound"] >= profile["settle_frame_gas"]:
        raise SystemExit("settlement frame does not cover the conservative fork bound")
    # The state dimension is only declared by profiles that have one, and when a profile
    # has one it is mandatory: a spec-profile manifest silently missing `settle_frame_state_gas`
    # would deploy a pool whose settlement frame declares no state budget at all.
    if expected["settle_frame_state_gas"] is None:
        if "settle_frame_state_gas" in profile:
            raise SystemExit("profile declares a state budget it has no dimension for")
    else:
        if profile.get("settle_frame_state_gas") != expected["settle_frame_state_gas"]:
            raise SystemExit("settlement state gas does not match the immutable dispatcher profile")
        if profile["conservative_settle_state_bound"] >= profile["settle_frame_state_gas"]:
            raise SystemExit("settlement frame does not cover the conservative state bound")

    ceremony = manifest["ceremony"]
    if not manifest["production"]:
        if not args.allow_testbed:
            raise SystemExit("activation blocked: manifest is testbed-only")
    elif ceremony["phase2_contributions"] < 2 or not ceremony["independent_verification"]:
        raise SystemExit("activation blocked: production ceremony evidence is incomplete")

    print(json.dumps({"artifacts": "match", "profile": "match",
                      "production": manifest["production"]}, sort_keys=True))


if __name__ == "__main__":
    main()
