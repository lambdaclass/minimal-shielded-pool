// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Groth16Verifier} from "../src/Groth16Verifier.sol";

interface VmJson {
    function readFile(string calldata) external view returns (string memory);
    function parseJsonString(string calldata, string calldata) external pure returns (string memory);
    function parseJsonStringArray(string calldata, string calldata) external pure returns (string[] memory);
    function parseBytes32(string calldata) external pure returns (bytes32);
    function parseUint(string calldata) external pure returns (uint256);
}

contract VerifierCanonicalTest {
    VmJson constant vm = VmJson(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant Q = 21888242871839275222246405745257275088696311157297823662689037894645226208583;
    Groth16Verifier verifier;
    string fixture;

    function setUp() public {
        verifier = new Groth16Verifier();
        fixture = vm.readFile("../wallet/smoke_fixture.json");
    }

    function _u(string memory path) internal view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(fixture, path));
    }

    function _pair(string memory path) internal view returns (uint256[2] memory out) {
        string[] memory values = vm.parseJsonStringArray(fixture, path);
        out[0] = uint256(vm.parseBytes32(values[0]));
        out[1] = uint256(vm.parseBytes32(values[1]));
    }

    function _vector()
        internal
        view
        returns (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c, uint256[10] memory input)
    {
        a = _pair(".transfer.proof.pA");
        b = [_pair(".transfer.proof.pB[0]"), _pair(".transfer.proof.pB[1]")];
        c = _pair(".transfer.proof.pC");
        input = [
            _u(".transfer.nf1"),
            _u(".transfer.nf2"),
            _u(".transfer.out_cm1"),
            _u(".transfer.out_cm2"),
            _u(".transfer.root"),
            _u(".transfer.domain"),
            _u(".transfer.public_amount"),
            _u(".transfer.fee"),
            _u(".transfer.recipient"),
            _u(".transfer.authorizer")
        ];
    }

    function test_valid_fixture_verifies() public view {
        (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c, uint256[10] memory input) = _vector();
        require(verifier.verifyProof(a, b, c, input), "valid proof rejected");
    }

    function test_noncanonical_coordinate_alias_is_rejected() public view {
        (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c, uint256[10] memory input) = _vector();
        require(a[1] <= type(uint256).max - Q, "fixture cannot form alias");
        a[1] += Q;
        require(!verifier.verifyProof(a, b, c, input), "coordinate alias accepted");
    }

    function test_infinity_and_authorizer_mutation_are_rejected() public view {
        (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c, uint256[10] memory input) = _vector();
        uint256[2] memory zeroA;
        require(!verifier.verifyProof(zeroA, b, c, input), "point at infinity accepted");
        input[9] ^= 1;
        require(!verifier.verifyProof(a, b, c, input), "authorizer mutation accepted");
    }
}
