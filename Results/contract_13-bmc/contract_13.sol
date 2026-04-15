// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : (K)          Section 3.14(K) Of The Disclosure Schedule Contains A Complete And Accurate List As Of The Date Of This Agreement Of:
// Generated: 2026-04-15 11:23:50 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract AgreementAndPlanOfMerger {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1614297600; // February 26, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    bool private _confidentialityAcknowledged;
    address private _arbitrator;
    ContractState private _state = ContractState.Created;
    uint256 private _deadline;

    bool private _locked; // reentrancy guard
    address payable private _partyA;
    address payable private _partyB;

    modifier onlyParties() {
        if (!(msg.sender == _partyA) && !(msg.sender == _partyB)) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    constructor(address partyA_, address partyB_, address arbitrator_) {
        _partyA = payable(partyA_);
        _partyB = payable(partyB_);
        _arbitrator = arbitrator_;
        startDate = EFFECTIVE_DATE;
        _deadline = block.timestamp + 30 days; // Example: 30 days from now
    }

    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event DisputeRaised(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    event StateChanged(uint8 from, uint8 to);

    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    /// @notice Execute acknowledgeNonDisclosure operation.
    function acknowledgeNonDisclosure() external onlyParties {
        _confidentialityAcknowledged = true;
        emit NonDisclosureAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute dispute operation.
    function dispute() external onlyArbitrator {
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
        if (_state == ContractState.Completed || _state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        if (_state != ContractState.Active) revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
        if (!(principal > 0) || !(rate > 0)) revert InsufficientPayment(0, 1);
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        if (_state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
            if (!ok) revert InsufficientPayment(0, address(this).balance);
        }
        emit ContractTerminated(msg.sender, block.timestamp);
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (address partyA_, address partyB_, address arbitrator_, uint8 state_, uint256 amount_, uint256 deadline_) {
        return (_partyA, _partyB, _arbitrator, uint8(_state), 0, _deadline);
    }

    /// @notice Receive ETH deposits.
    receive() external payable {        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        emit PaymentReceived(msg.sender, msg.value);
    }
}
