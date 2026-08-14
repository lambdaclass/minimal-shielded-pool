// SPDX-License-Identifier: GPL-3.0
/*
    Copyright 2021 0KIMS association.

    This file is generated with [snarkJS](https://github.com/iden3/snarkjs).

    snarkJS is a free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    snarkJS is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
    or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
    License for more details.

    You should have received a copy of the GNU General Public License
    along with snarkJS. If not, see <https://www.gnu.org/licenses/>.
*/

pragma solidity >=0.7.0 <0.9.0;

contract Groth16Verifier {
    // Scalar field size
    uint256 constant r = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    // Base field size
    uint256 constant q = 21888242871839275222246405745257275088696311157297823662689037894645226208583;

    // Verification Key data
    uint256 constant alphax = 10899631952996201649638567342879419727880265375950810036909905183458505397999;
    uint256 constant alphay = 13466781178189826433367411859693159479856006443211467902397175356327766918391;
    uint256 constant betax1 = 7980791852383058201332583331692157608179448398960750522582495895986214042501;
    uint256 constant betax2 = 21058877660691153884771517678139805936888059802534672268098545283310828942646;
    uint256 constant betay1 = 19201035858842119346600140100215826890935972401629665670733175969196373661389;
    uint256 constant betay2 = 2373779605368475221018428688280409663437464211402265791354657095008510203949;
    uint256 constant gammax1 = 11559732032986387107991004021392285783925812861821192530917403151452391805634;
    uint256 constant gammax2 = 10857046999023057135944570762232829481370756359578518086990519993285655852781;
    uint256 constant gammay1 = 4082367875863433681332203403145435568316851327593401208105741076214120093531;
    uint256 constant gammay2 = 8495653923123431417604973247489272438418190587263600148770280649306958101930;
    uint256 constant deltax1 = 20712356707541417199457212751456051242770182596214166874061067691285891644949;
    uint256 constant deltax2 = 272725788839098547220536926577533629376842515339586399755979238665725493922;
    uint256 constant deltay1 = 18661219868138736393649890530832423639867859195191103688294162428804470343079;
    uint256 constant deltay2 = 21213099457450664805676468697139911861168781623878873518313681010175009366067;

    uint256 constant IC0x = 15967006749767389580435721196243919308589584456428825906211235712392838887701;
    uint256 constant IC0y = 3073801712547654050497694580027901471066704508975571179284818226671241839028;

    uint256 constant IC1x = 14169873647905806168622048348397190890396374011963816537844833251082816409826;
    uint256 constant IC1y = 5935411653031504500322265313929219631967349364878170296730905367672019267056;

    uint256 constant IC2x = 12402267679861650758949292365718872602453734277979613582496522927506093510717;
    uint256 constant IC2y = 21104476848366193917998113435727847406175140680073612191197567735807298976775;

    uint256 constant IC3x = 5782807423334927559470732422415896169471069519787934722247525765986754279223;
    uint256 constant IC3y = 3049525123619864205012831380526907301972654722019901828033041960277691811211;

    uint256 constant IC4x = 8561790506575717214224814551352392553192328742838273124986766438971444630171;
    uint256 constant IC4y = 13580907056768802211843407028435382898853208611694672433390618439219171536052;

    uint256 constant IC5x = 503480208211244984192530418906171530633082099776773103218299831223315709041;
    uint256 constant IC5y = 10637978097647662872839948305710716890342641529668111348286453275197054354626;

    uint256 constant IC6x = 11989474171747089705592905843560376996858259436464466928320567030382027478589;
    uint256 constant IC6y = 3864675540725966146807003641379096076109226561350496380050467504396296796151;

    uint256 constant IC7x = 3586394210649607065228516981171748518094731907487786407562115437389005610891;
    uint256 constant IC7y = 7457939195306771936254731134750754416882335386098290347586357619778376279054;

    uint256 constant IC8x = 9516982749459936247499360008053636379857270885847510114015646423249678131871;
    uint256 constant IC8y = 4143635036554294241440564296763855976835218758640997071900968700353998067352;

    uint256 constant IC9x = 15401889580180237579766496878785075902074001681340046446118890204002506308701;
    uint256 constant IC9y = 9533008685068664167824847538559050511228277092167693893435451596178559792687;

    uint256 constant IC10x = 386230572310883069447014930044302411288976349101663609138509127024752552532;
    uint256 constant IC10y = 4211802497807762387483567695894189605238950225324832023252557138872891559021;

    // Memory data
    uint16 constant pVk = 0;
    uint16 constant pPairing = 128;

    uint16 constant pLastMem = 896;

    function verifyProof(
        uint256[2] calldata _pA,
        uint256[2][2] calldata _pB,
        uint256[2] calldata _pC,
        uint256[10] calldata _pubSignals
    ) public view returns (bool) {
        assembly {
            function checkField(v) {
                if iszero(lt(v, r)) {
                    mstore(0, 0)
                    return(0, 0x20)
                }
            }

            // G1 function to multiply a G1 value(x,y) to value in an address
            function g1_mulAccC(pR, x, y, s) {
                let success
                let mIn := mload(0x40)
                mstore(mIn, x)
                mstore(add(mIn, 32), y)
                mstore(add(mIn, 64), s)

                success := staticcall(500000, 7, mIn, 96, mIn, 64)

                if iszero(success) {
                    mstore(0, 0)
                    return(0, 0x20)
                }

                mstore(add(mIn, 64), mload(pR))
                mstore(add(mIn, 96), mload(add(pR, 32)))

                success := staticcall(500000, 6, mIn, 128, pR, 64)

                if iszero(success) {
                    mstore(0, 0)
                    return(0, 0x20)
                }
            }

            function checkPairing(pA, pB, pC, pubSignals, pMem) -> isOk {
                let _pPairing := add(pMem, pPairing)
                let _pVk := add(pMem, pVk)

                mstore(_pVk, IC0x)
                mstore(add(_pVk, 32), IC0y)

                // Compute the linear combination vk_x

                g1_mulAccC(_pVk, IC1x, IC1y, calldataload(add(pubSignals, 0)))

                g1_mulAccC(_pVk, IC2x, IC2y, calldataload(add(pubSignals, 32)))

                g1_mulAccC(_pVk, IC3x, IC3y, calldataload(add(pubSignals, 64)))

                g1_mulAccC(_pVk, IC4x, IC4y, calldataload(add(pubSignals, 96)))

                g1_mulAccC(_pVk, IC5x, IC5y, calldataload(add(pubSignals, 128)))

                g1_mulAccC(_pVk, IC6x, IC6y, calldataload(add(pubSignals, 160)))

                g1_mulAccC(_pVk, IC7x, IC7y, calldataload(add(pubSignals, 192)))

                g1_mulAccC(_pVk, IC8x, IC8y, calldataload(add(pubSignals, 224)))

                g1_mulAccC(_pVk, IC9x, IC9y, calldataload(add(pubSignals, 256)))

                g1_mulAccC(_pVk, IC10x, IC10y, calldataload(add(pubSignals, 288)))

                // -A
                mstore(_pPairing, calldataload(pA))
                mstore(add(_pPairing, 32), mod(sub(q, calldataload(add(pA, 32))), q))

                // B
                mstore(add(_pPairing, 64), calldataload(pB))
                mstore(add(_pPairing, 96), calldataload(add(pB, 32)))
                mstore(add(_pPairing, 128), calldataload(add(pB, 64)))
                mstore(add(_pPairing, 160), calldataload(add(pB, 96)))

                // alpha1
                mstore(add(_pPairing, 192), alphax)
                mstore(add(_pPairing, 224), alphay)

                // beta2
                mstore(add(_pPairing, 256), betax1)
                mstore(add(_pPairing, 288), betax2)
                mstore(add(_pPairing, 320), betay1)
                mstore(add(_pPairing, 352), betay2)

                // vk_x
                mstore(add(_pPairing, 384), mload(add(pMem, pVk)))
                mstore(add(_pPairing, 416), mload(add(pMem, add(pVk, 32))))

                // gamma2
                mstore(add(_pPairing, 448), gammax1)
                mstore(add(_pPairing, 480), gammax2)
                mstore(add(_pPairing, 512), gammay1)
                mstore(add(_pPairing, 544), gammay2)

                // C
                mstore(add(_pPairing, 576), calldataload(pC))
                mstore(add(_pPairing, 608), calldataload(add(pC, 32)))

                // delta2
                mstore(add(_pPairing, 640), deltax1)
                mstore(add(_pPairing, 672), deltax2)
                mstore(add(_pPairing, 704), deltay1)
                mstore(add(_pPairing, 736), deltay2)

                let success := staticcall(500000, 8, _pPairing, 768, _pPairing, 0x20)

                isOk := and(success, mload(_pPairing))
            }

            // CANONICAL_PROOF_COORDINATES: the pairing precompile accepts
            // field elements, but a proof has one canonical uint256 encoding.
            // Reject aliases such as pA.y + q and reject infinity points.
            function checkCoordinate(v) {
                if iszero(lt(v, q)) {
                    mstore(0, 0)
                    return(0, 0x20)
                }
            }
            let ax := calldataload(_pA)
            let ay := calldataload(add(_pA, 32))
            let bx0 := calldataload(_pB)
            let bx1 := calldataload(add(_pB, 32))
            let by0 := calldataload(add(_pB, 64))
            let by1 := calldataload(add(_pB, 96))
            let cx := calldataload(_pC)
            let cy := calldataload(add(_pC, 32))
            checkCoordinate(ax)
            checkCoordinate(ay)
            checkCoordinate(bx0)
            checkCoordinate(bx1)
            checkCoordinate(by0)
            checkCoordinate(by1)
            checkCoordinate(cx)
            checkCoordinate(cy)
            if iszero(or(ax, ay)) {
                mstore(0, 0)
                return(0, 0x20)
            }
            if iszero(or(or(bx0, bx1), or(by0, by1))) {
                mstore(0, 0)
                return(0, 0x20)
            }
            if iszero(or(cx, cy)) {
                mstore(0, 0)
                return(0, 0x20)
            }

            let pMem := mload(0x40)
            mstore(0x40, add(pMem, pLastMem))

            // Validate that all evaluations ∈ F

            checkField(calldataload(add(_pubSignals, 0)))

            checkField(calldataload(add(_pubSignals, 32)))

            checkField(calldataload(add(_pubSignals, 64)))

            checkField(calldataload(add(_pubSignals, 96)))

            checkField(calldataload(add(_pubSignals, 128)))

            checkField(calldataload(add(_pubSignals, 160)))

            checkField(calldataload(add(_pubSignals, 192)))

            checkField(calldataload(add(_pubSignals, 224)))

            checkField(calldataload(add(_pubSignals, 256)))

            checkField(calldataload(add(_pubSignals, 288)))

            // Validate all evaluations
            let isValid := checkPairing(_pA, _pB, _pC, _pubSignals, pMem)

            mstore(0, isValid)
            return(0, 0x20)
        }
    }
}
