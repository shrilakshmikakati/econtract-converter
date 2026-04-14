// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-14 06:47:37 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract MergerAndAcquisition {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    address private _arbitrator;
    uint256 public constant EFFECTIVE_DATE = 1618704000; // April 18, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    bool private _locked;

    event DisputeRaised(address indexed party);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 principal, uint256 rate, uint256 penalty);

    address payable private _partyA;
    address payable private _partyB;
    uint256 private _deadline; // contract expiry (unix timestamp)
    ContractState private state;

    modifier onlyParties() {
        if (!(msg.sender == _partyA || msg.sender == _partyB)) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    constructor(address arbitrator) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = arbitrator;
        state = ContractState.Created;
    }

    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    /// @notice Execute dispute operation.
    function dispute() external onlyParties {
        if (state == ContractState.Disputed) revert AlreadyDisputed();
        state = ContractState.Disputed;
        emit DisputeRaised(msg.sender);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        if (state != ContractState.Active) revert InvalidState(uint8(state), uint8(ContractState.Active));
        state = ContractState.Completed;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external noReentrant returns (uint256 penalty) {
        if (state != ContractState.Disputed)
            revert InvalidState(uint8(state), uint8(ContractState.Disputed));
        if (block.timestamp <= _deadline) return 0;
        uint256 daysLate = (block.timestamp - _deadline) / 1 days;
        penalty = (principal * rate * daysLate) / 10_000;
        emit PenaltyCalculated(principal, rate, penalty);
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties noReentrant {
        if (state == ContractState.Terminated)
            revert InvalidState(uint8(state), uint8(ContractState.Active));
        state = ContractState.Terminated;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (
        address partyA_,
        address partyB_,
        address arbitrator_,
        uint8   state_,
        uint256 amount_,
        uint256 deadline_
    ) {
        return (_partyA, _partyB, _arbitrator, uint8(state), 0, _deadline);
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
