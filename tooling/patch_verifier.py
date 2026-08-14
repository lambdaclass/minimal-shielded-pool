#!/usr/bin/env python3
"""Harden snarkjs's generated verifier for the restricted VERIFY prefix.

The Hegotá validation observer bans the GAS opcode. snarkjs emits
`staticcall(sub(gas(), 2000), ...)` for ECADD, ECMUL, and pairing. Replace all
three with a fixed request; EIP-150 still caps it to 63/64 of remaining gas.
It also rejects noncanonical BN254 point-coordinate aliases and infinity
encodings before the pairing precompile. Fail closed if a future snarkjs output
changes shape.
"""
from pathlib import Path

VERIFIER = Path(__file__).parent.parent / "contracts" / "src" / "Groth16Verifier.sol"
NEEDLE = "staticcall(sub(gas(), 2000),"
REPLACEMENT = "staticcall(500000,"
LEGACY_REPLACEMENT = "staticcall(30000000,"


def main():
    source = VERIFIER.read_text()
    if source.count(LEGACY_REPLACEMENT) == 3:
        source = source.replace(LEGACY_REPLACEMENT, REPLACEMENT)
        print("tightened 3 legacy verifier gas requests to 500000")
    else:
        count = source.count(NEEDLE)
        if count == 3:
            source = source.replace(NEEDLE, REPLACEMENT)
            print("patched 3 verifier precompile calls to fixed gas")
        elif count == 0 and source.count(REPLACEMENT) == 3:
            print("verifier already uses 3 fixed-gas precompile calls")
        else:
            raise SystemExit(f"expected 3 snarkjs GAS calls, found {count}; inspect generated verifier")

    marker = "// CANONICAL_PROOF_COORDINATES"
    if marker not in source:
        needle = """            let pMem := mload(0x40)\n            mstore(0x40, add(pMem, pLastMem))\n"""
        guard = """            // CANONICAL_PROOF_COORDINATES: the pairing precompile accepts\n            // field elements, but a proof has one canonical uint256 encoding.\n            // Reject aliases such as pA.y + q and reject infinity points.\n            function checkCoordinate(v) {\n                if iszero(lt(v, q)) {\n                    mstore(0, 0)\n                    return(0, 0x20)\n                }\n            }\n            let ax := calldataload(_pA)\n            let ay := calldataload(add(_pA, 32))\n            let bx0 := calldataload(_pB)\n            let bx1 := calldataload(add(_pB, 32))\n            let by0 := calldataload(add(_pB, 64))\n            let by1 := calldataload(add(_pB, 96))\n            let cx := calldataload(_pC)\n            let cy := calldataload(add(_pC, 32))\n            checkCoordinate(ax)\n            checkCoordinate(ay)\n            checkCoordinate(bx0)\n            checkCoordinate(bx1)\n            checkCoordinate(by0)\n            checkCoordinate(by1)\n            checkCoordinate(cx)\n            checkCoordinate(cy)\n            if iszero(or(ax, ay)) { mstore(0, 0) return(0, 0x20) }\n            if iszero(or(or(bx0, bx1), or(by0, by1))) { mstore(0, 0) return(0, 0x20) }\n            if iszero(or(cx, cy)) { mstore(0, 0) return(0, 0x20) }\n\n            let pMem := mload(0x40)\n            mstore(0x40, add(pMem, pLastMem))\n"""
        if source.count(needle) != 1:
            raise SystemExit("generated verifier memory prologue changed; inspect before hardening")
        source = source.replace(needle, guard)
        print("added canonical BN254 coordinate and infinity checks")
    else:
        print("verifier already has canonical proof-coordinate checks")

    VERIFIER.write_text(source)


if __name__ == "__main__":
    main()
