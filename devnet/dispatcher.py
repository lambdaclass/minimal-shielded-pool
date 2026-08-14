#!/usr/bin/env python3
"""Compile and parameterize ShieldedPoolDispatcher.yul (the pool-as-sender
shell that DELEGATECALLs settlement to ShieldedPoolLogic.sol).

The deployed runtime carries two appended immutables: the implementation
(ShieldedPoolLogic) address and the Groth16 verifier address, each a 32-byte
word (impl first, verifier second). The verifier is a dispatcher immutable
because frame-0 proof verification is inline (a direct staticcall to the
verifier): a delegatecall to the implementation during a VERIFY frame is
rejected by ethrex's validation observer.

Usage:
  dispatcher.py --initcode 0x<impl> 0x<verifier>   deploy initcode
  dispatcher.py --artifact            write the bare initcode (no tail) to
                                      build/shielded_pool_dispatcher_init.hex
                                      for the forge suite
                                      (contracts/test/DispatcherPool.t.sol).
                                      Rerun after any ShieldedPoolDispatcher.yul
                                      change.

There is deliberately NO --runtime mode: as with yul_pool.py, the optimizer
appends a data segment after the runtime subobject, so the naive 0xfe-split is
unsound. Derive the expected deployed code by simulating the deployment:

  cast call --rpc-url <rpc> --create "$(dispatcher.py --initcode 0x<impl>)"

run_live_dispatcher.sh verifies the deployed dispatcher this way.
"""
import argparse
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

SOLC_VERSION = "0.8.30"


def address(value: str) -> int:
    parsed = int(value, 16)
    if parsed <= 0 or parsed >= 1 << 160:
        raise SystemExit(f"invalid address: {value}")
    return parsed


def solc_binary() -> str:
    candidates = [
        os.environ.get("SOLC"), shutil.which("solc"),
        str(Path.home() / "Library" / "Application Support" / "svm" /
            SOLC_VERSION / f"solc-{SOLC_VERSION}"),
        str(Path.home() / ".svm" / SOLC_VERSION / f"solc-{SOLC_VERSION}"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            version = subprocess.run([candidate, "--version"], capture_output=True,
                                     text=True, check=True).stdout
            if f"Version: {SOLC_VERSION}" not in version:
                raise SystemExit(f"expected solc {SOLC_VERSION}, got: {version.strip()}")
            return candidate
    raise SystemExit(f"solc {SOLC_VERSION} not found; set SOLC to the pinned binary")


@lru_cache(maxsize=1)
def _compiled() -> bytes:
    source = Path(__file__).with_name("ShieldedPoolDispatcher.yul")
    result = subprocess.run(
        [solc_binary(), "--strict-assembly", "--optimize", "--optimize-runs", "200", "--bin", source.name],
        cwd=source.parent, capture_output=True, text=True, check=True,
    )
    marker = "Binary representation:\n"
    if marker not in result.stdout:
        raise SystemExit(f"could not parse solc output: {result.stdout}\n{result.stderr}")
    return bytes.fromhex(result.stdout.split(marker, 1)[1].splitlines()[0].strip())


def initcode(impl: int, verifier: int) -> bytes:
    return _compiled() + impl.to_bytes(32, "big") + verifier.to_bytes(32, "big")


def write_artifact() -> Path:
    out = Path(__file__).with_name("build") / "shielded_pool_dispatcher_init.hex"
    out.parent.mkdir(exist_ok=True)
    out.write_text("0x" + _compiled().hex())  # no trailing newline: vm.parseBytes reads it whole
    return out


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--initcode", action="store_true")
    g.add_argument("--artifact", action="store_true")
    ap.add_argument("addrs", nargs="*", help="implementation (ShieldedPoolLogic) address")
    a = ap.parse_args()
    if a.artifact:
        print(write_artifact())
        return
    if len(a.addrs) != 2:
        ap.error("--initcode needs 2 addresses: ShieldedPoolLogic implementation, Groth16 verifier")
    print("0x" + initcode(address(a.addrs[0]), address(a.addrs[1])).hex())


if __name__ == "__main__":
    main()
