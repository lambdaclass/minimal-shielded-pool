/// @title ShieldedPoolDispatcher
/// @notice Immutable EIP-8141 validation shell for the minimal pool, at the
/// spec's current pins.
/// @dev The pre-relaunch dispatcher is archived byte-exact under
/// devnet/vectors/2026-09-01-hegota-final-profile/. The two are not
/// interchangeable and neither can read the other's transactions: this one nests the
/// fee fields, gives every frame `limits = [execution, state]`, renumbers three
/// TXPARAM indices, and moves RECENTROOTREFLOAD to 0xB6. Deploy exactly one, to
/// match the chain.
/// Runtime tail: implementation address || Groth16 verifier address.
object "ShieldedPoolDispatcher" {
    code {
        // ShieldedPoolLogic.currentRoot is storage slot 22.
        sstore(22, 0x2134e76ac5d21aab186c2be1dd8f84ee880a1e46eaf712f9d371b6df22191f3e)

        let rsize := datasize("runtime")
        codecopy(rsize, sub(codesize(), 64), 64)
        datacopy(0, dataoffset("runtime"), rsize)
        return(0, add(rsize, 64))
    }

    object "runtime" {
        code {
            function txParam(param) -> value { value := verbatim_1i_1o(hex"B0", param) }
            function frameParam(frameIndex, param) -> value {
                value := verbatim_2i_1o(hex"B3", frameIndex, param)
            }
            function frameDataLoad(frameIndex, offset) -> value {
                value := verbatim_2i_1o(hex"B1", offset, frameIndex)
            }
            function sigParam(signatureIndex, param) -> value {
                value := verbatim_2i_1o(hex"B4", signatureIndex, param)
            }
            function recentRootRef(field, index) -> value {
                // 0xB6 now: EIP-8141 took 0xB5 for SIGDATACOPY, so EIP-8272's
                // RECENTROOTREFLOAD moved along (upstream 0231fb05f5).
                value := verbatim_2i_1o(hex"B6", field, index)
            }
            function approveExecutionAndPayment() { verbatim_3i_0o(hex"AA", 0, 0, 3) }

            function fail(sel) {
                mstore(0, shl(224, sel))
                revert(0, 4)
            }
            function errShape() -> s { s := 0xe6d22e28 }
            function errRoot() -> s { s := 0xaf501e1c }
            function errProof() -> s { s := 0x7fcdd1f4 }
            function errKeys() -> s { s := 0x586e51ed }
            function errCanonical() -> s { s := 0xd7c7beeb }
            function errValue() -> s { s := 0x2ad907fb }
            function errFee() -> s { s := 0x315cb54e }
            function errDomain() -> s { s := 0xeb127982 }
            function errNullifier() -> s { s := 0xcbbbbfe1 }
            function errAuthorizer() -> s { s := 0xb4683784 }

            function scalarField() -> v {
                v := 21888242871839275222246405745257275088548364400416034343698204186575808495617
            }
            function baseField() -> v {
                v := 21888242871839275222246405745257275088696311157297823662689037894645226208583
            }
            function maxValue() -> v { v := 0x100000000000000000000000000000000 }
            function domainTag() -> v {
                v := 0x40752e102d2a749c61d42a71e297edd3b493de639003b9480a700d589d98065b
            }

            // EIP-8272: keccak256(address20(pool) || bytes32(epoch)).
            function sourceId(epoch) -> id {
                mstore(0, shl(96, address()))
                mstore(0x14, epoch)
                id := keccak256(0, 0x34)
            }

            // Stable nullifier domain: chain + immutable pool, never epoch.
            function domainVal() -> d {
                mstore(0, domainTag())
                mstore(0x20, chainid())
                mstore(0x40, address())
                d := mod(keccak256(0, 0x60), scalarField())
            }

            function impl() -> a {
                codecopy(0, sub(codesize(), 64), 32)
                a := and(mload(0), 0xffffffffffffffffffffffffffffffffffffffff)
            }
            function verifierAddr() -> a {
                codecopy(0, sub(codesize(), 32), 32)
                a := and(mload(0), 0xffffffffffffffffffffffffffffffffffffffff)
            }

            function verifyProof(settleIndex) {
                // settle((root,rootSlot,epoch,domain,nf1,nf2,out1,out2,
                //         publicAmount,fee,recipient,authorizer))
                let root := frameDataLoad(settleIndex, 4)
                let rootSlot := frameDataLoad(settleIndex, 36)
                let epoch := frameDataLoad(settleIndex, 68)
                let dom := frameDataLoad(settleIndex, 100)
                let nf1 := frameDataLoad(settleIndex, 132)
                let nf2 := frameDataLoad(settleIndex, 164)
                let out1 := frameDataLoad(settleIndex, 196)
                let out2 := frameDataLoad(settleIndex, 228)
                let pub := frameDataLoad(settleIndex, 260)
                let fee := frameDataLoad(settleIndex, 292)
                let recipient := frameDataLoad(settleIndex, 324)
                let authorizer := frameDataLoad(settleIndex, 356)

                if iszero(nf1) { fail(errNullifier()) }
                if iszero(nf2) { fail(errNullifier()) }
                if or(shr(64, rootSlot), shr(64, epoch)) { fail(errCanonical()) }
                if or(iszero(authorizer), shr(160, authorizer)) { fail(errAuthorizer()) }
                if shr(160, recipient) { fail(errCanonical()) }
                if iszero(eq(iszero(pub), iszero(recipient))) { fail(errShape()) }
                if iszero(eq(dom, domainVal())) { fail(errDomain()) }

                let p := scalarField()
                if iszero(lt(root, p)) { fail(errCanonical()) }
                if iszero(lt(dom, p)) { fail(errCanonical()) }
                if iszero(lt(nf1, p)) { fail(errCanonical()) }
                if iszero(lt(nf2, p)) { fail(errCanonical()) }
                if iszero(lt(out1, p)) { fail(errCanonical()) }
                if iszero(lt(out2, p)) { fail(errCanonical()) }
                if iszero(lt(pub, maxValue())) { fail(errValue()) }
                if iszero(lt(fee, maxValue())) { fail(errValue()) }

                // Reject non-canonical field aliases and points at infinity
                // before invoking the generated Groth16 verifier.
                let q := baseField()
                for { let o := 0 } lt(o, 256) { o := add(o, 32) } {
                    if iszero(lt(calldataload(o), q)) { fail(errProof()) }
                }
                if iszero(or(calldataload(0), calldataload(32))) { fail(errProof()) }
                if iszero(or(or(calldataload(64), calldataload(96)), or(calldataload(128), calldataload(160)))) {
                    fail(errProof())
                }
                if iszero(or(calldataload(192), calldataload(224))) { fail(errProof()) }

                let m := 0x80
                mstore(m, shl(224, 0xf3bb70f6))
                calldatacopy(add(m, 4), 0, 256)
                mstore(add(m, 0x104), nf1)
                mstore(add(m, 0x124), nf2)
                mstore(add(m, 0x144), out1)
                mstore(add(m, 0x164), out2)
                mstore(add(m, 0x184), root)
                mstore(add(m, 0x1a4), dom)
                mstore(add(m, 0x1c4), pub)
                mstore(add(m, 0x1e4), fee)
                mstore(add(m, 0x204), recipient)
                mstore(add(m, 0x224), authorizer)

                let ok := staticcall(500000, verifierAddr(), m, 0x244, 0, 32)
                if iszero(ok) { fail(errProof()) }
                if iszero(eq(returndatasize(), 32)) { fail(errProof()) }
                if iszero(eq(mload(0), 1)) { fail(errProof()) }
            }

            function verifyFrameApprove() {
                // One immutable two-frame, self-paying grammar.
                if iszero(eq(txParam(0x02), address())) { fail(errShape()) }
                if iszero(eq(txParam(0x09), 2)) { fail(errShape()) }
                if txParam(0x0A) { fail(errShape()) }
                if iszero(eq(txParam(0x0B), 1)) { fail(errShape()) }
                if txParam(0x07) { fail(errShape()) }
                if iszero(eq(txParam(0x0E), 2)) { fail(errKeys()) }
                if txParam(0x01) { fail(errKeys()) }
                if iszero(eq(txParam(0x11), 1)) { fail(errRoot()) }

                // The sole low-s secp256k1 signature is protocol-validated over
                // the canonical transaction hash. Its signer is proof-selected.
                // Its length is not checked: the protocol fixes it at 65 bytes,
                // and SIGPARAM exposes len(signature) for ARBITRARY entries only.
                let authorizer := frameDataLoad(1, 356)
                if or(iszero(authorizer), shr(160, authorizer)) { fail(errAuthorizer()) }
                if iszero(eq(sigParam(0, 0), authorizer)) { fail(errAuthorizer()) }
                if iszero(eq(sigParam(0, 1), 1)) { fail(errAuthorizer()) }
                if sigParam(0, 2) { fail(errAuthorizer()) }

                // Frame 0: proof-carrying VERIFY by the pool.
                if iszero(eq(frameParam(0, 0x00), address())) { fail(errShape()) }
                if iszero(eq(frameParam(0, 0x01), 320000)) { fail(errShape()) }
                // The proof frame writes nothing, so it declares no state budget.
                // Any nonzero value here would be `max_gas` the pool pays for and
                // cannot use.
                if frameParam(0, 0x09) { fail(errShape()) }
                if iszero(eq(frameParam(0, 0x02), 1)) { fail(errShape()) }
                if iszero(eq(frameParam(0, 0x03), 3)) { fail(errShape()) }
                if iszero(eq(frameParam(0, 0x04), 256)) { fail(errShape()) }
                if frameParam(0, 0x08) { fail(errShape()) }

                // Frame 1: the single settlement call, with fork-profile gas.
                if iszero(eq(frameParam(1, 0x00), address())) { fail(errShape()) }
                if iszero(eq(frameParam(1, 0x01), 1400000)) { fail(errShape()) }
                // Settlement's state growth is bounded at five new slots
                // (finalized root, epoch counter, two leaf markers, one withdrawal
                // credit); 550000 covers 5 * 64 * 1530 with margin. Pinned for the
                // same reason as the execution budget: unpinned, it is the pool's
                // money.
                if iszero(eq(frameParam(1, 0x09), 550000)) { fail(errShape()) }
                if iszero(eq(frameParam(1, 0x02), 2)) { fail(errShape()) }
                if frameParam(1, 0x03) { fail(errShape()) }
                if iszero(eq(frameParam(1, 0x04), 388)) { fail(errShape()) }
                if frameParam(1, 0x08) { fail(errShape()) }
                if iszero(eq(shr(224, frameDataLoad(1, 0)), 0x921fcac7)) { fail(errShape()) }

                // The consumed EIP-8250 key set is exactly the two nullifiers.
                let nf1 := frameDataLoad(1, 132)
                let nf2 := frameDataLoad(1, 164)
                let lo := nf1
                let hi := nf2
                if gt(lo, hi) { lo := nf2 hi := nf1 }
                mstore(0, 2)
                mstore(0x20, lo)
                mstore(0x40, hi)
                if iszero(eq(txParam(0x0F), keccak256(0, 0x60))) { fail(errKeys()) }

                // Bind the exact EIP-8272 tuple, including slot and epoch source.
                let rootSlot := frameDataLoad(1, 36)
                let epoch := frameDataLoad(1, 68)
                if or(shr(64, rootSlot), shr(64, epoch)) { fail(errCanonical()) }
                if iszero(eq(recentRootRef(0, 0), sourceId(epoch))) { fail(errRoot()) }
                if iszero(eq(recentRootRef(1, 0), rootSlot)) { fail(errRoot()) }
                if iszero(eq(recentRootRef(2, 0), frameDataLoad(1, 4))) { fail(errRoot()) }

                verifyProof(1)
                if lt(frameDataLoad(1, 292), txParam(0x06)) { fail(errFee()) }
                approveExecutionAndPayment()
            }

            if eq(calldatasize(), 256) {
                verifyFrameApprove()
                stop()
            }

            let target := impl()
            calldatacopy(0, 0, calldatasize())
            let ok := delegatecall(0x1c9c380, target, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch ok
            case 0 { revert(0, returndatasize()) }
            default { return(0, returndatasize()) }
        }
    }
}
