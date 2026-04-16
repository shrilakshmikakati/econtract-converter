// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-16 04:16:23 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract MergerAgreement {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    address private _arbitrator;
    uint256 public constant EFFECTIVE_DATE = 1611705600; // January 27, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    bool private _locked;
    uint256 private _deadline;

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    ContractState private _state;

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

    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    event StateChanged(uint8 from, uint8 to);

    constructor(address payable partyA_, address payable partyB_, address arbitrator_) {
        _partyA = partyA_;
        _partyB = partyB_;
        _arbitrator = arbitrator_;
        startDate = EFFECTIVE_DATE;
        _state = ContractState.Created;
    }

    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    /// @notice Execute acknowledgeNonDisclosure operation.
    function acknowledgeNonDisclosure() external onlyParties {
        bool private _confidentialityAcknowledged;
        emit NonDisclosureAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute dispute operation.
    function dispute() external onlyArbitrator {
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
        if (_state != ContractState.Active) revert InvalidState(uint8(_state), uint8(ContractState.Active));
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
        return (_partyA, _partyB, _arbitrator, uint8(_state), _amount, _deadline);
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
