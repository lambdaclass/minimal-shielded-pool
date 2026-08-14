#!/usr/bin/env python3
"""Fail-closed artifact, ceremony, and gas-profile activation gate."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    if profile["wire_profile"] != "ethrex-v23-hegota-testnet":
        raise SystemExit("unsupported transaction wire profile")
    if required > profile["hegota_profile_2_budget"]:
        raise SystemExit("transaction exceeds the configured Hegota Profile 2 budget")
    if profile["max_observed_verify_gas"] >= profile["verify_frame_gas"]:
        raise SystemExit("VERIFY frame does not cover the observed valid path")
    if profile["settle_frame_gas"] != 2_000_000:
        raise SystemExit("settlement gas does not match the immutable dispatcher profile")
    if profile["conservative_settle_bound"] >= profile["settle_frame_gas"]:
        raise SystemExit("settlement frame does not cover the conservative fork bound")

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
