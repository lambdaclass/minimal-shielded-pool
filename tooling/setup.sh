#!/usr/bin/env bash
# Compile the spend circuit and run a TESTBED-ONLY Groth16 setup.
#
#   ./setup.sh                  # compile + ceremony + verifier export
#
# Groth16 needs a circuit-specific trusted setup. This script runs the whole
# ceremony locally with entropy from /dev/urandom, which is fine for a testbed
# and for Sepolia demos but is NOT a production ceremony: whoever runs it
# could keep the toxic waste and forge proofs for themselves. A real
# deployment replaces phase 1 with a public powers-of-tau (e.g. the Hermez
# ceremony file) and runs a multi-party phase 2. Set PTAU=/path/to/final.ptau
# to use an externally sourced phase-1 file.
#
# Outputs (build/ is gitignored except the two committed artifacts):
#   build/spend.r1cs, build/spend_js/spend.wasm   circuit
#   build/spend_final.zkey                        proving key (~8 MB)
#   ../contracts/src/Groth16Verifier.sol          committed, snarkjs-generated
#   ../contracts/vectors/spend_vkey.json          committed verification key
#
# The committed verifier and any committed proof fixtures are mutually
# consistent; re-running this script re-randomises the ceremony, so it also
# regenerates every proof fixture consumer (run ../wallet/gen_smoke.py after).
set -euo pipefail
cd "$(dirname "$0")"

BUILD=../build
mkdir -p "$BUILD"

echo "==> compiling spend.circom (circom $(npx circom2 --version | tail -1 | awk '{print $3}'))"
npx circom2 ../circuits/spend.circom --r1cs --wasm --sym -l node_modules -o "$BUILD"
npx snarkjs r1cs info "$BUILD/spend.r1cs"

if [ -n "${PTAU:-}" ]; then
  echo "==> using external powers of tau: $PTAU"
  cp "$PTAU" "$BUILD/pot_final.ptau"
else
  echo "==> TESTBED phase 1: local powers of tau (power 14)"
  npx snarkjs powersoftau new bn128 14 "$BUILD/pot14_0.ptau" -v >/dev/null
  npx snarkjs powersoftau contribute "$BUILD/pot14_0.ptau" "$BUILD/pot14_1.ptau" \
    --name="testbed" -e="$(head -c 64 /dev/urandom | base64)" >/dev/null
  npx snarkjs powersoftau prepare phase2 "$BUILD/pot14_1.ptau" "$BUILD/pot_final.ptau" -v >/dev/null
fi

echo "==> phase 2: circuit-specific zkey"
npx snarkjs groth16 setup "$BUILD/spend.r1cs" "$BUILD/pot_final.ptau" "$BUILD/spend_0.zkey" >/dev/null
npx snarkjs zkey contribute "$BUILD/spend_0.zkey" "$BUILD/spend_final.zkey" \
  --name="testbed" -e="$(head -c 64 /dev/urandom | base64)" >/dev/null
npx snarkjs zkey verify "$BUILD/spend.r1cs" "$BUILD/pot_final.ptau" "$BUILD/spend_final.zkey" >/dev/null

echo "==> exporting the verification key and the Solidity verifier"
npx snarkjs zkey export verificationkey "$BUILD/spend_final.zkey" ../contracts/vectors/spend_vkey.json
npx snarkjs zkey export solidityverifier "$BUILD/spend_final.zkey" ../contracts/src/Groth16Verifier.sol
python3 patch_verifier.py
( cd ../contracts && forge fmt src/Groth16Verifier.sol )

echo "==> done: build/spend_final.zkey (proving), contracts/src/Groth16Verifier.sol (on-chain)"
