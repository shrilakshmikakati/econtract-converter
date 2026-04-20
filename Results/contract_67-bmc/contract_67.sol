// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Article Iv.
// Generated: 2026-04-20 07:55:04 UTC
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

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1608422400; // December 20, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    bool private _locked;
    address private _arbitrator;
    uint256 private _deadline; // contract expiry (unix timestamp)

    event DisputeRaised(address indexed party, uint256 timestamp);
    event ContractCompleted(address indexed party, uint256 timestamp);
    event PenaltyCalculated(uint256 amount, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);

    modifier onlyParties() {
        if (!(msg.sender == address(this)) && !(msg.sender == _arbitrator)) revert Unauthorized();
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
        emit DisputeRaised(msg.sender, block.timestamp);
        setState(ContractState.Disputed);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        setState(ContractState.Completed);
        emit ContractCompleted(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator {
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty, block.timestamp);
        // Logic to handle the penalty
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        setState(ContractState.Terminated);
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (ContractState) {
        return state;
    }

    /// @notice Execute pay operation.
    function pay() external payable noReentrant {
        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        setState(ContractState.Active);
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Receive ETH deposits.
    receive() external payable {        // ETH received
    }

    function setState(ContractState newState) private {
        state = newState;
    }

    ContractState public state;

    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
