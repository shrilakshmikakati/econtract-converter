// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Execution Version
// Generated: 2026-04-20 09:25:12 UTC
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
    uint256 public constant EFFECTIVE_DATE = 1615680000; // March 14, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "Delaware";
    bool private _locked;
    address private _arbitrator;
    uint256 private _deadline;

    address payable private _partyA;
    address payable private _partyB;
    ContractState private _state = ContractState.Created;

    modifier onlyParties() {
        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event CompletedAcknowledged(address indexed caller, uint256 value);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event Terminated(address indexed caller, uint256 value);

    constructor(address partyA_, address partyB_, address arbitrator_) {
        _partyA = payable(partyA_);
        _partyB = payable(partyB_);
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
        bool _confidentialityAcknowledged;
        emit NonDisclosureAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute dispute operation.
    function dispute() external onlyArbitrator {
        ContractState state = getContractState();
        if (!(state == ContractState.Active) && !(state == ContractState.Completed)) revert Unauthorized();
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        ContractState state = getContractState();
        if (state != ContractState.Active) revert Unauthorized();
        // Logic to confirm milestone
        emit CompletedAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator {
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        ContractState state = getContractState();
        if (!(state == ContractState.Active) && !(state == ContractState.Completed)) revert Unauthorized();
        // Logic to handle termination
        emit Terminated(msg.sender, block.timestamp);
    }

    /// @notice Execute getContractState operation.
    function getContractState() public view returns (uint8) {
        return uint8(_state);
    }

    /// @notice Receive ETH deposits.
    receive() external payable {}

    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
