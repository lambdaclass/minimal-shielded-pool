#!/usr/bin/env bash
# End-to-end smoke: real Groth16 proofs through the whole BN254 stack.
#
#   1. compile the circuit + run the (testbed) trusted setup if not done
#   2. generate fresh witnesses from the reconstructed tree, prove them
#      (real Groth16), verify each off-chain, write wallet/smoke_fixture.json
#   3. run the on-chain verifier and settlement safety suites in an in-process
#      EVM via Forge, including a real proof (no attester)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f ../build/spend_final.zkey ]; then
  echo "==> no zkey; compiling circuit + running testbed setup"
  ( cd ../tooling && ./setup.sh )
fi

echo "==> generating real proofs and fixture (${1:-deterministic})"
if [ "${1:-}" = "--random" ]; then
  python3 gen_smoke.py --random
else
  python3 gen_smoke.py
fi

echo "==> running on-chain verifier and settlement safety tests"
( cd ../contracts && forge test -vv )

echo "==> smoke passed: real Groth16 proofs produced, verified off-chain,"
echo "    and verified ON-CHAIN alongside the settlement safety suite."
