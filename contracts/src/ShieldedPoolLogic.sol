// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Settlement logic for the immutable pool-as-sender dispatcher.
/// The dispatcher verifies the proof and complete FrameTx envelope. This
/// implementation performs only bounded internal state changes after approval.
contract ShieldedPoolLogic {
    uint32 public constant DEPTH = 20;
    uint32 public constant CAPACITY = uint32(1) << DEPTH;
    uint256 internal constant P = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    uint256 internal constant MAX_VALUE = 1 << 128;
    bytes32 public constant EMPTY_ROOT = 0x2134e76ac5d21aab186c2be1dd8f84ee880a1e46eaf712f9d371b6df22191f3e;
    bytes32 public constant DOMAIN_TAG = 0x40752e102d2a749c61d42a71e297edd3b493de639003b9480a700d589d98065b;
    bytes32 public constant SINK_0 = 0x23f1b896ada6ee5dac80945b11329e7ab64412c2be9f5c87cfa3261cc1d8216f;
    bytes32 public constant SINK_1 = 0x2fd476622c67c880b3049a76c7337192362834c9d6dfb55c5060bb96c98932bb;
    address public constant RECENT_ROOT_PREDEPLOY = address(0x8272);

    address private immutable IMPLEMENTATION_SELF = address(this);
    address public immutable POSEIDON_T3;
    address public immutable POSEIDON_T4;

    // The first 25 slots deliberately preserve the original dispatcher layout.
    bytes32[DEPTH + 1] public filledSubtrees; // slots 0..20
    uint32 public nextIndex; // slot 21
    bytes32 public currentRoot; // slot 22
    mapping(bytes32 => bool) public isLeaf; // slot 23, global across epochs
    mapping(address => uint256) public withdrawalCredit; // slot 24
    uint64 public currentEpoch; // slot 25
    mapping(uint64 => bytes32) public finalRoot; // slot 26

    struct Spend {
        bytes32 root;
        uint64 rootSlot;
        uint64 epoch;
        bytes32 domain;
        bytes32 nf1;
        bytes32 nf2;
        bytes32 outCm1;
        bytes32 outCm2;
        uint256 publicAmount;
        uint256 fee;
        address recipient;
        address authorizer;
    }

    event LeafAppended(bytes32 indexed cm, uint64 indexed epoch, uint32 index, bytes32 newRoot);
    event NoteSpent(bytes32 indexed nf);
    event EpochRolled(uint64 indexed closedEpoch, bytes32 finalRoot, uint64 indexed newEpoch);
    event RootPublished(uint64 indexed epoch, bytes32 indexed source, bytes32 root);
    event WithdrawalCredited(address indexed recipient, uint256 amount);
    event Withdrawn(address indexed recipient, uint256 amount);

    error DirectImplementationCall();
    error ZeroValueShield();
    error ValueTooLarge();
    error DuplicateCommitment();
    error ReservedSink();
    error NotCanonical();
    error NotPoolSender();
    error ZeroNullifier();
    error InvalidDomain();
    error InvalidEpoch();
    error InvalidAuthorizer();
    error InvalidSettlementShape();
    error InvalidRoot();
    error RootPublishFailed();
    error NoCredit();
    error PayoutFailed();
    error InvalidHashLibrary();
    error HashFailed();

    modifier onlyDelegate() {
        if (address(this) == IMPLEMENTATION_SELF) revert DirectImplementationCall();
        _;
    }

    /// @dev The dispatcher constructor initializes slot 22 to EMPTY_ROOT.
    constructor(address poseidonT3, address poseidonT4) {
        if (poseidonT3.code.length == 0 || poseidonT4.code.length == 0) revert InvalidHashLibrary();
        POSEIDON_T3 = poseidonT3;
        POSEIDON_T4 = poseidonT4;
    }

    function sourceId(uint64 epoch) public view returns (bytes32) {
        return keccak256(abi.encodePacked(address(this), bytes32(uint256(epoch))));
    }

    function domain() public view returns (bytes32) {
        return domainFor(block.chainid, address(this));
    }

    function domainFor(uint256 chainId, address pool) public pure returns (bytes32) {
        return bytes32(uint256(keccak256(abi.encodePacked(DOMAIN_TAG, chainId, bytes32(uint256(uint160(pool)))))) % P);
    }

    function shield(bytes32 inner) external payable onlyDelegate returns (uint32 index) {
        if (msg.value == 0) revert ZeroValueShield();
        if (msg.value >= MAX_VALUE) revert ValueTooLarge();
        if (uint256(inner) >= P) revert NotCanonical();

        bytes32 cm = bytes32(_hash3(2, uint256(inner), msg.value));
        if (cm == SINK_0 || cm == SINK_1) revert ReservedSink();
        if (isLeaf[cm]) revert DuplicateCommitment();

        _ensureCapacity(1);
        index = _insert(cm);
        currentRoot = _computeRoot();
        emit LeafAppended(cm, currentEpoch, index, currentRoot);
    }

    /// @notice The only post-approval settlement entrypoint.
    /// The immutable dispatcher has already checked the proof, complete
    /// envelope, signature, nonce keys, recent-root tuple, and gas constants.
    function settle(Spend calldata s) external onlyDelegate {
        if (msg.sender != address(this)) revert NotPoolSender();
        if (s.nf1 == bytes32(0) || s.nf2 == bytes32(0)) revert ZeroNullifier();
        if (s.authorizer == address(0)) revert InvalidAuthorizer();
        if (s.domain != domain()) revert InvalidDomain();
        if (s.epoch > currentEpoch) revert InvalidEpoch();
        if (
            uint256(s.root) >= P || uint256(s.domain) >= P || uint256(s.nf1) >= P || uint256(s.nf2) >= P
                || uint256(s.outCm1) >= P || uint256(s.outCm2) >= P
        ) revert NotCanonical();
        if (s.publicAmount >= MAX_VALUE || s.fee >= MAX_VALUE) revert ValueTooLarge();
        if ((s.publicAmount == 0) != (s.recipient == address(0))) revert InvalidSettlementShape();
        if (s.outCm1 == s.outCm2 || s.outCm1 == SINK_1 || s.outCm2 == SINK_0) {
            revert InvalidSettlementShape();
        }
        if ((s.outCm1 != SINK_0 && isLeaf[s.outCm1]) || (s.outCm2 != SINK_1 && isLeaf[s.outCm2])) {
            revert DuplicateCommitment();
        }

        emit NoteSpent(s.nf1);
        emit NoteSpent(s.nf2);

        uint32 appendCount;
        if (s.outCm1 != SINK_0) appendCount++;
        if (s.outCm2 != SINK_1) appendCount++;
        _ensureCapacity(appendCount);

        bool new1 = s.outCm1 != SINK_0;
        bool new2 = s.outCm2 != SINK_1;
        uint32 i1;
        uint32 i2;
        if (new1) i1 = _insert(s.outCm1);
        if (new2) i2 = _insert(s.outCm2);
        if (new1 || new2) {
            currentRoot = _computeRoot();
            if (new1) emit LeafAppended(s.outCm1, currentEpoch, i1, currentRoot);
            if (new2) emit LeafAppended(s.outCm2, currentEpoch, i2, currentRoot);
        }

        if (s.publicAmount != 0) {
            withdrawalCredit[s.recipient] += s.publicAmount;
            emit WithdrawalCredited(s.recipient, s.publicAmount);
        }
    }

    /// @notice Publish an authenticated active or finalized epoch root.
    /// This is deliberately outside settlement, so publication failure cannot
    /// consume approved note keys without creating the promised outputs.
    function publishEpochRoot(uint64 epoch) external onlyDelegate {
        bytes32 root;
        if (epoch == currentEpoch) root = currentRoot;
        else if (epoch < currentEpoch) root = finalRoot[epoch];
        else revert InvalidEpoch();
        if (root == bytes32(0)) revert InvalidRoot();

        bytes32 salt = bytes32(uint256(epoch));
        (bool ok,) = RECENT_ROOT_PREDEPLOY.call(abi.encodePacked(salt, root));
        if (!ok) revert RootPublishFailed();
        emit RootPublished(epoch, sourceId(epoch), root);
    }

    function claimWithdrawal(address payable who) external onlyDelegate {
        uint256 amount = withdrawalCredit[who];
        if (amount == 0) revert NoCredit();
        withdrawalCredit[who] = 0;
        emit Withdrawn(who, amount);
        (bool ok,) = who.call{value: amount}("");
        if (!ok) revert PayoutFailed();
    }

    function _ensureCapacity(uint32 count) internal {
        if (count > CAPACITY - nextIndex) _rollEpoch();
    }

    function _rollEpoch() internal {
        uint64 closed = currentEpoch;
        bytes32 closedRoot = currentRoot;
        finalRoot[closed] = closedRoot;
        currentEpoch = closed + 1;
        nextIndex = 0;
        currentRoot = EMPTY_ROOT;
        for (uint32 l = 0; l <= DEPTH; l++) {
            delete filledSubtrees[l];
        }
        emit EpochRolled(closed, closedRoot, currentEpoch);
    }

    function _hashPair(bytes32 l, bytes32 r) internal view returns (bytes32) {
        return bytes32(_hash2(uint256(l), uint256(r)));
    }

    function _hash2(uint256 x0, uint256 x1) internal view returns (uint256 out) {
        (bool ok, bytes memory ret) =
            POSEIDON_T3.staticcall{gas: 200_000}(abi.encodeWithSelector(bytes4(0x511c53ff), x0, x1));
        if (!ok || ret.length != 32) revert HashFailed();
        out = abi.decode(ret, (uint256));
        if (out >= P) revert HashFailed();
    }

    function _hash3(uint256 x0, uint256 x1, uint256 x2) internal view returns (uint256 out) {
        (bool ok, bytes memory ret) =
            POSEIDON_T4.staticcall{gas: 200_000}(abi.encodeWithSelector(bytes4(0x2dbf86c6), x0, x1, x2));
        if (!ok || ret.length != 32) revert HashFailed();
        out = abi.decode(ret, (uint256));
        if (out >= P) revert HashFailed();
    }

    function _insert(bytes32 cm) internal returns (uint32 index) {
        index = nextIndex;
        nextIndex = index + 1;
        isLeaf[cm] = true;
        bytes32 node = cm;
        uint32 idx = index;
        uint32 l;
        while (idx & 1 == 1) {
            node = _hashPair(filledSubtrees[l], node);
            idx >>= 1;
            l++;
        }
        filledSubtrees[l] = node;
    }

    function _computeRoot() internal view returns (bytes32 node) {
        uint32 idx = nextIndex;
        if (idx == CAPACITY) return filledSubtrees[DEPTH];
        for (uint32 l = 0; l < DEPTH; l++) {
            node = idx & 1 == 0 ? _hashPair(node, _zeros(l)) : _hashPair(filledSubtrees[l], node);
            idx >>= 1;
        }
    }

    function _zeros(uint32 l) internal pure returns (bytes32) {
        if (l == 0) return bytes32(0);
        if (l == 1) return 0x2098f5fb9e239eab3ceac3f27b81e481dc3124d55ffed523a839ee8446b64864;
        if (l == 2) return 0x1069673dcdb12263df301a6ff584a7ec261a44cb9dc68df067a4774460b1f1e1;
        if (l == 3) return 0x18f43331537ee2af2e3d758d50f72106467c6eea50371dd528d57eb2b856d238;
        if (l == 4) return 0x07f9d837cb17b0d36320ffe93ba52345f1b728571a568265caac97559dbc952a;
        if (l == 5) return 0x2b94cf5e8746b3f5c9631f4c5df32907a699c58c94b2ad4d7b5cec1639183f55;
        if (l == 6) return 0x2dee93c5a666459646ea7d22cca9e1bcfed71e6951b953611d11dda32ea09d78;
        if (l == 7) return 0x078295e5a22b84e982cf601eb639597b8b0515a88cb5ac7fa8a4aabe3c87349d;
        if (l == 8) return 0x2fa5e5f18f6027a6501bec864564472a616b2e274a41211a444cbe3a99f3cc61;
        if (l == 9) return 0x0e884376d0d8fd21ecb780389e941f66e45e7acce3e228ab3e2156a614fcd747;
        if (l == 10) return 0x1b7201da72494f1e28717ad1a52eb469f95892f957713533de6175e5da190af2;
        if (l == 11) return 0x1f8d8822725e36385200c0b201249819a6e6e1e4650808b5bebc6bface7d7636;
        if (l == 12) return 0x2c5d82f66c914bafb9701589ba8cfcfb6162b0a12acf88a8d0879a0471b5f85a;
        if (l == 13) return 0x14c54148a0940bb820957f5adf3fa1134ef5c4aaa113f4646458f270e0bfbfd0;
        if (l == 14) return 0x190d33b12f986f961e10c0ee44d8b9af11be25588cad89d416118e4bf4ebe80c;
        if (l == 15) return 0x22f98aa9ce704152ac17354914ad73ed1167ae6596af510aa5b3649325e06c92;
        if (l == 16) return 0x2a7c7c9b6ce5880b9f6f228d72bf6a575a526f29c66ecceef8b753d38bba7323;
        if (l == 17) return 0x2e8186e558698ec1c67af9c14d463ffc470043c9c2988b954d75dd643f36b992;
        if (l == 18) return 0x0f57c5571e9a4eab49e2c8cf050dae948aef6ead647392273546249d1c1ff10f;
        if (l == 19) return 0x1830ee67b5fb554ad5f63d4388800e1cfe78e310697d46e43c9ce36134f72cca;
        return EMPTY_ROOT;
    }
}
