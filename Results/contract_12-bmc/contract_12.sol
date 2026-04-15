// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Execution Version Confidential
// Generated: 2026-04-15 11:37:33 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract Agreement {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1608249600; // December 18, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "an";
    bool private _locked;
    address private _arbitrator;
    uint256 private _deadline;

    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event AgreementTerminated(address indexed caller, uint256 value);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event StateChanged(uint8 from, uint8 to);

    address payable private _partyA;
    address payable private _partyB;
    ContractState private _state = ContractState.Created;

    modifier onlyParties() {
        if (!(msg.sender == _partyA) && !(msg.sender == _partyB)) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    constructor(address partyA_, address partyB_, address arbitrator_) {
        _partyA = partyA_;
        _partyB = partyB_;
        _arbitrator = arbitrator_;
        startDate = EFFECTIVE_DATE;
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
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyParties returns (uint256) {
        if (!(principal > 0) || !(rate > 0)) revert Unauthorized();
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties noReentrant {
        _locked = true;
        emit AgreementTerminated(msg.sender, block.timestamp);
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (ContractState) {
        if (_locked) return ContractState.Terminated;
        // Add logic to determine the current state
        return ContractState.Active;
    }

    /// @notice Receive ETH deposits.
    receive() external payable {        // ETH received
    }

    /// @notice Execute pay operation.
    function pay() external payable onlyParties noReentrant {
        if (msg.value == 0) revert InsufficientPayment(0, _amount);
        _state = ContractState.Active;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Execute setDeadline operation.
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
