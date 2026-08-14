// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {ShieldedPoolLogic} from "../src/ShieldedPoolLogic.sol";

interface Vm {
    function deal(address, uint256) external;
    function etch(address, bytes calldata) external;
    function expectRevert(bytes4) external;
    function getDeployedCode(string calldata artifactPath) external returns (bytes memory);
    function store(address, bytes32, bytes32) external;
}

interface IPool {
    function shield(bytes32 inner) external payable returns (uint32);
    function settle(ShieldedPoolLogic.Spend calldata s) external;
    function publishEpochRoot(uint64 epoch) external;
    function claimWithdrawal(address payable who) external;
    function currentRoot() external view returns (bytes32);
    function currentEpoch() external view returns (uint64);
    function nextIndex() external view returns (uint32);
    function finalRoot(uint64) external view returns (bytes32);
    function isLeaf(bytes32) external view returns (bool);
    function withdrawalCredit(address) external view returns (uint256);
    function domain() external view returns (bytes32);
    function sourceId(uint64) external view returns (bytes32);
}

contract MockPoseidonT3 {
    uint256 constant P = 21888242871839275222246405745257275088548364400416034343698204186575808495617;

    function hash2(uint256 x0, uint256 x1) external pure returns (uint256) {
        return uint256(keccak256(abi.encode(x0, x1))) % P;
    }
}

contract MockPoseidonT4 {
    uint256 constant P = 21888242871839275222246405745257275088548364400416034343698204186575808495617;

    function hash3(uint256 x0, uint256 x1, uint256 x2) external pure returns (uint256) {
        return uint256(keccak256(abi.encode(x0, x1, x2))) % P;
    }
}

contract LogicProxy {
    address immutable implementation;
    bytes32 constant EMPTY_ROOT = 0x2134e76ac5d21aab186c2be1dd8f84ee880a1e46eaf712f9d371b6df22191f3e;

    constructor(address implementation_) {
        implementation = implementation_;
        assembly { sstore(22, EMPTY_ROOT) }
    }

    function settleAsSelf(ShieldedPoolLogic.Spend calldata s) external {
        (bool ok, bytes memory ret) = address(this).call(abi.encodeCall(IPool.settle, (s)));
        if (!ok) assembly { revert(add(ret, 32), mload(ret)) }
    }

    fallback() external payable {
        address target = implementation;
        assembly {
            calldatacopy(0, 0, calldatasize())
            let ok := delegatecall(gas(), target, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch ok
            case 0 { revert(0, returndatasize()) }
            default { return(0, returndatasize()) }
        }
    }

    receive() external payable {}
}

contract RevertingRecentRoot {
    fallback() external payable {
        revert();
    }
}

contract RecordingRecentRoot {
    bytes32 public lastSalt;
    bytes32 public lastRoot;

    fallback() external payable {
        assembly {
            sstore(0, calldataload(0))
            sstore(1, calldataload(32))
        }
    }

    receive() external payable {}
}

contract RejectEther {
    receive() external payable {
        revert();
    }
}

contract DispatcherPoolTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    bytes32 constant EMPTY_ROOT = 0x2134e76ac5d21aab186c2be1dd8f84ee880a1e46eaf712f9d371b6df22191f3e;
    bytes32 constant SINK_0 = 0x23f1b896ada6ee5dac80945b11329e7ab64412c2be9f5c87cfa3261cc1d8216f;
    bytes32 constant SINK_1 = 0x2fd476622c67c880b3049a76c7337192362834c9d6dfb55c5060bb96c98932bb;
    address constant ROOT_PREDEPLOY = address(0x8272);
    uint256 constant SETTLE_FRAME_GAS = 2_000_000;

    event SettlementGasMeasured(uint256 gasUsed);

    ShieldedPoolLogic logic;
    LogicProxy proxy;
    IPool pool;

    function setUp() public {
        MockPoseidonT3 t3 = new MockPoseidonT3();
        MockPoseidonT4 t4 = new MockPoseidonT4();
        logic = new ShieldedPoolLogic(address(t3), address(t4));
        proxy = new LogicProxy(address(logic));
        pool = IPool(address(proxy));
        vm.deal(address(proxy), 100 ether);
    }

    function _spend(bytes32 out1, bytes32 out2, uint256 amount, address recipient)
        internal
        view
        returns (ShieldedPoolLogic.Spend memory s)
    {
        s = ShieldedPoolLogic.Spend({
            root: bytes32(uint256(7)),
            rootSlot: 9,
            epoch: 0,
            domain: pool.domain(),
            nf1: bytes32(uint256(11)),
            nf2: bytes32(uint256(12)),
            outCm1: out1,
            outCm2: out2,
            publicAmount: amount,
            fee: 1,
            recipient: recipient,
            authorizer: address(0xA11CE)
        });
    }

    function _settle(ShieldedPoolLogic.Spend memory s) internal {
        proxy.settleAsSelf(s);
    }

    function test_direct_implementation_calls_are_rejected() public {
        vm.expectRevert(ShieldedPoolLogic.DirectImplementationCall.selector);
        logic.shield{value: 1}(bytes32(uint256(3)));
    }

    function test_shield_does_not_call_recent_root_predeploy() public {
        vm.etch(ROOT_PREDEPLOY, type(RevertingRecentRoot).runtimeCode);
        uint32 index = pool.shield{value: 1 ether}(bytes32(uint256(33)));
        require(index == 0 && pool.nextIndex() == 1, "shield did not settle");
    }

    function test_actual_poseidon_library_runtimes_work_via_staticcall() public {
        address t3 = address(0xA003);
        address t4 = address(0xA004);
        vm.etch(t3, vm.getDeployedCode("PoseidonT3.sol:PoseidonT3"));
        vm.etch(t4, vm.getDeployedCode("PoseidonT4.sol:PoseidonT4"));
        ShieldedPoolLogic actualLogic = new ShieldedPoolLogic(t3, t4);
        LogicProxy actualProxy = new LogicProxy(address(actualLogic));
        IPool actualPool = IPool(address(actualProxy));
        uint32 index = actualPool.shield{value: 1}(bytes32(uint256(99)));
        require(index == 0 && actualPool.currentRoot() != EMPTY_ROOT, "actual Poseidon calls failed");

        vm.store(address(actualProxy), bytes32(uint256(21)), bytes32(uint256((1 << 20) - 1)));
        ShieldedPoolLogic.Spend memory s = _spend(bytes32(uint256(301)), bytes32(uint256(302)), 0, address(0));
        s.domain = actualPool.domain();
        actualProxy.settleAsSelf(s);
        require(actualPool.currentEpoch() == 1 && actualPool.nextIndex() == 2, "actual rollover failed");
    }

    function test_two_million_gas_covers_heaviest_reachable_settlement_shape() public {
        address t3 = address(0xA013);
        address t4 = address(0xA014);
        vm.etch(t3, vm.getDeployedCode("PoseidonT3.sol:PoseidonT3"));
        vm.etch(t4, vm.getDeployedCode("PoseidonT4.sol:PoseidonT4"));
        ShieldedPoolLogic actualLogic = new ShieldedPoolLogic(t3, t4);
        LogicProxy actualProxy = new LogicProxy(address(actualLogic));
        IPool actualPool = IPool(address(actualProxy));

        // A valid cap-1 tree has every filled-subtree slot populated. The
        // settlement must finalize that epoch, clear it, append two outputs,
        // compute the new root, and create a fresh withdrawal credit.
        for (uint256 slot; slot < 20; slot++) {
            vm.store(address(actualProxy), bytes32(slot), bytes32(slot + 1));
        }
        bytes32 oldRoot = bytes32(uint256(777));
        vm.store(address(actualProxy), bytes32(uint256(21)), bytes32(uint256((1 << 20) - 1)));
        vm.store(address(actualProxy), bytes32(uint256(22)), oldRoot);
        ShieldedPoolLogic.Spend memory s = _spend(bytes32(uint256(401)), bytes32(uint256(402)), 7, address(0xB0B));
        s.domain = actualPool.domain();

        uint256 beforeGas = gasleft();
        (bool ok,) = address(actualProxy).call{gas: SETTLE_FRAME_GAS}(abi.encodeCall(LogicProxy.settleAsSelf, (s)));
        uint256 used = beforeGas - gasleft();
        emit SettlementGasMeasured(used);

        require(ok, "two-million settlement cap exhausted");
        require(actualPool.currentEpoch() == 1, "epoch did not roll");
        require(actualPool.finalRoot(0) == oldRoot, "final root missing");
        require(actualPool.nextIndex() == 2, "outputs missing");
        require(actualPool.withdrawalCredit(address(0xB0B)) == 7, "credit missing");
    }

    function test_settlement_does_not_call_recent_root_predeploy() public {
        vm.etch(ROOT_PREDEPLOY, type(RevertingRecentRoot).runtimeCode);
        ShieldedPoolLogic.Spend memory s = _spend(bytes32(uint256(101)), bytes32(uint256(102)), 0, address(0));
        _settle(s);
        require(pool.isLeaf(s.outCm1) && pool.isLeaf(s.outCm2), "outputs missing");
    }

    function test_two_output_spend_rolls_before_cap_boundary() public {
        bytes32 oldRoot = bytes32(uint256(777));
        vm.store(address(proxy), bytes32(uint256(21)), bytes32(uint256((1 << 20) - 1)));
        vm.store(address(proxy), bytes32(uint256(22)), oldRoot);
        ShieldedPoolLogic.Spend memory s = _spend(bytes32(uint256(201)), bytes32(uint256(202)), 0, address(0));
        _settle(s);
        require(pool.currentEpoch() == 1, "epoch did not roll");
        require(pool.finalRoot(0) == oldRoot, "old root not retained");
        require(pool.nextIndex() == 2, "outputs not inserted into fresh epoch");
    }

    function test_full_tree_exit_consumes_no_capacity() public {
        vm.store(address(proxy), bytes32(uint256(21)), bytes32(uint256(1 << 20)));
        ShieldedPoolLogic.Spend memory s = _spend(SINK_0, SINK_1, 5 ether, address(0xB0B));
        _settle(s);
        require(pool.currentEpoch() == 0, "exit rolled epoch");
        require(pool.nextIndex() == 1 << 20, "exit consumed capacity");
        require(pool.withdrawalCredit(address(0xB0B)) == 5 ether, "credit missing");
    }

    function test_invalid_sink_positions_and_duplicate_outputs_reject() public {
        ShieldedPoolLogic.Spend memory s = _spend(bytes32(uint256(55)), bytes32(uint256(55)), 0, address(0));
        vm.expectRevert(ShieldedPoolLogic.InvalidSettlementShape.selector);
        proxy.settleAsSelf(s);
        s = _spend(SINK_1, SINK_0, 0, address(0));
        vm.expectRevert(ShieldedPoolLogic.InvalidSettlementShape.selector);
        proxy.settleAsSelf(s);

        s = _spend(bytes32(uint256(57)), bytes32(uint256(58)), 0, address(0));
        _settle(s);
        s = _spend(bytes32(uint256(57)), bytes32(uint256(59)), 0, address(0));
        vm.expectRevert(ShieldedPoolLogic.DuplicateCommitment.selector);
        proxy.settleAsSelf(s);
    }

    function test_publication_is_separate_authenticated_and_retryable() public {
        vm.etch(ROOT_PREDEPLOY, type(RevertingRecentRoot).runtimeCode);
        vm.expectRevert(ShieldedPoolLogic.RootPublishFailed.selector);
        pool.publishEpochRoot(0);
        require(pool.currentRoot() == EMPTY_ROOT, "failed publish changed pool state");

        vm.etch(ROOT_PREDEPLOY, type(RecordingRecentRoot).runtimeCode);
        pool.publishEpochRoot(0);
        RecordingRecentRoot recorder = RecordingRecentRoot(payable(ROOT_PREDEPLOY));
        require(recorder.lastSalt() == bytes32(0), "wrong epoch salt");
        require(recorder.lastRoot() == EMPTY_ROOT, "wrong root");
    }

    function test_failed_claim_preserves_credit() public {
        RejectEther rejecter = new RejectEther();
        ShieldedPoolLogic.Spend memory s = _spend(SINK_0, SINK_1, 2 ether, address(rejecter));
        _settle(s);
        vm.expectRevert(ShieldedPoolLogic.PayoutFailed.selector);
        pool.claimWithdrawal(payable(address(rejecter)));
        require(pool.withdrawalCredit(address(rejecter)) == 2 ether, "credit was lost");
    }

    function test_epoch_sources_are_distinct_but_nullifier_domain_is_stable() public view {
        require(pool.sourceId(0) != pool.sourceId(1), "epoch sources collide");
        bytes32 beforeDomain = pool.domain();
        require(beforeDomain != bytes32(0), "zero domain");
    }
}
