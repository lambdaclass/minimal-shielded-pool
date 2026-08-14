# Hardened minimal pool live run

This is disposable testnet evidence, not a production deployment. The proving
key is the repository's single-party testbed key.

## Deployment

- Network: Hegotá, chain ID 8141
- Pool: `0x60e3a56106d53ac5c7a7a4d15b0af61e0f5b96f4`
- Dispatcher code hash: `0x22c4d883ae26cb158a49642464b4dbdbde552d28cebc80652b51fde7296d4078`
- Verifier: `0x44fD39E840714584fbD880ADF0A0132F804a0891`
- Verifier code hash: `0x2f07433c0f11cc413d7001f1b37f87931e40e9eb907ff1dc87bf4b9f368d9c9a`
- Poseidon T3: `0xEF3382A3c067c54dEbE0dAE1F3daAe9D7a088eAc`
- Poseidon T3 code hash: `0xaffeee3a4ac5a7d9eb1361900710aa5d2d06b0770d3f023b17fed993452dfce4`
- Poseidon T4: `0xFaFc2D59b164f437444E731a44Ee404996C0d51d`
- Poseidon T4 code hash: `0x93e9a1cfb84f2be03554d8f0e06164fa8ba2bcc6f3cac89a9f7756d91fdace33`
- Settlement logic: `0xF12e598460db0C8F366aC7E6354EeFcaC4f93aA0`
- Settlement code hash: `0xb3a8f0e62a20aaa83326f8eaa94b903a456a122fedc364289014936e94857165`

The deployment script checked the verifier and dispatcher byte-for-byte. It
checked the address-bearing Solidity library runtimes after separating their
embedded self-address, and checked every non-immutable logic byte plus both
Poseidon immutable getters.

## Flow

| Step | Transaction | Result |
|---|---|---|
| Shield 1 ETH | `0x43a953943ca45845e105267f963008ab465256d0f9bd045f9ccf119bf8fd4378` | Passed, 1,099,624 gas |
| Publish post-shield root | deployment script | Passed, EIP-7843 slot 39,553 |
| Private transfer | `0x3318f589b59d52825285a71b5cfd0dcbfd99ebee19aefa1a18c9b0d057657301` | Passed, 1,427,661 gas; VERIFY frame 294,401 gas |
| Publish post-transfer root | `0xf392a089fecd065a4ae1bb1d03d795cd65d7ee332a8be88c6c60e9bcdfc6c62d` | Passed, EIP-7843 slot 39,561 |
| Private withdrawal | `0xc80872c7c49b47690b0eb084f009acd6553ea55062f2c9391810322459533718` | Passed, 457,222 gas; VERIFY frame 294,374 gas |
| Claim 0.55 ETH | `0xa240a045d5dddb909fe7f087c1ebbd064f565564194030d5a4032a2a32366ecd` | Passed, 215,776 gas |
| Replay transfer | same signed raw transfer | Rejected: keyed nonce expected sequence 1, transaction carried 0 |

The transfer and withdrawal used different circuit-selected one-time signers.
Both used the pool as EIP-8141 sender and payer, consumed exactly two EIP-8250
keys, and bound one EIP-8272 reference. The wallet read `slotNumber` directly;
it performed no timestamp conversion.

Final state was `nextIndex = 3`, `currentEpoch = 0`, and withdrawal credit was
zero after the claim. The sink withdrawal inserted no leaves. The pool balance
was exactly `448115116986805819` wei:

`1 ETH - 0.55 ETH - (1,427,661 + 457,222) * 1,000,000,007 wei/gas`.

The declared validation budget is 400,000 gas plus the 2,800-gas secp256k1
signature cost. This is below EIP-8369's testnet Profile 2 cap of 2^20 and was
accepted by the current testnet configuration. It is above EIP-8141's published
100,000-gas public-mempool policy and therefore is not portable to an
unmodified EIP-8141 public mempool.
