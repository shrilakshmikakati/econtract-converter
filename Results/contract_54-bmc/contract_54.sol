// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Ex-2.1
// Generated: 2026-04-17 11:16:23 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract BankMerger {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1619395200; // April 26, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "and";
    bool private _locked;
    address private _arbitrator;

    event DisputeRaised(address indexed party, uint256 timestamp);
    event ContractCompleted(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);

    address payable private _partyA;
    uint256 private _deadline; // contract expiry (unix timestamp)
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

    constructor(address payable partyA_, address payable partyB_, address arbitrator_) {
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

    /// @notice Execute dispute operation.
    function dispute() external onlyParties {
        emit DisputeRaised(msg.sender, block.timestamp);
        _state = ContractState.Disputed;
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit ContractCompleted(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
        if (!(principal > 0) || !(rate > 0)) revert Unauthorized();
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
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

    /// @notice Execute pay operation.
    function pay(uint256 amount) external onlyParties payable noReentrant {
        if (msg.value != amount) revert InsufficientPayment(msg.value, amount);
        _state = ContractState.Active;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Execute setDeadline operation.
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
