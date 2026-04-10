// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Electronic Contract
// Generated: 2026-04-09 11:52:09 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deploym.
.ent on mainnet.
// =================================================================

contract PaymentContract {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1593561600; // JULY 1, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "the";
    bool private _locked;
    address private _arbitrator;
    address payable private _partyA;
    address payable private _partyB;
    uint256 private _deadline; // contract expiry (unix timestamp)
    ContractState private state;

    event DisputeRaised(address indexed party);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);

    modifier onlyParties() {
        if (!(msg.sender == _partyA || msg.sender == _partyB)) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    constructor(address arbitrator, address partyA_, address partyB_) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = arbitrator;
        _partyA = payable(partyA_);
        _partyB = payable(partyB_);
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
        emit DisputeRaised(msg.sender);
        state = ContractState.Disputed;
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        state = ContractState.Completed;
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
        if (!(principal > 0 && rate > 0)) revert InsufficientPayment(0, 1);
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        state = ContractState.Terminated;
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (address, address, address, uint8, uint256, uint256) {
        return (_partyA, _partyB, _arbitrator, uint8(state), _amount, _deadline);
    }

    /// @notice Receive ETH deposits.
    receive() external payable {        emit PaymentReceived(msg.sender, msg.value);
    }

    uint256 private _amount;

    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }

    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);
        state = ContractState.Active;
        emit PaymentReceived(msg.sender, msg.value);
    }
}
