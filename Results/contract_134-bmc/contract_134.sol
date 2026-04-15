// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Annex B-1     Annex C   Tender And Support Agreement
// Generated: 2026-04-15 11:11:28 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract PascalCaseContract {
    error AlreadyDisputed();
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error InvalidState(uint8 current, uint8 required);
    error ReentrantCall();
    error Unauthorized();

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1614643200; // March 2, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "and";
    address private _arbitrator;
    bool private _locked;

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
    event Completed(address indexed caller, uint256 value);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event StateChanged(uint8 from, uint8 to);
    event Terminated(address indexed caller, uint256 value);

    address private partyA;
    address private partyB;
    uint256 private _deadline; // contract expiry (unix timestamp)

    constructor(address _partyA, address _partyB, address _arbitrator) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = _arbitrator;
        partyA = _partyA;
        partyB = _partyB;
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
        ContractState state = getContractState();
        if (!(state == ContractState.Active) && !(state == ContractState.Disputed)) revert Unauthorized();
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        ContractState state = getContractState();
        if (state != ContractState.Active) revert Unauthorized();
        // Logic to confirm delivery
        emit Completed(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256 penalty) {
        penalty = principal * rate;
        emit PenaltyCalculated(principal, rate, penalty, block.timestamp);
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        ContractState state = getContractState();
        if (!(state == ContractState.Active) && !(state == ContractState.Disputed)) revert Unauthorized();
        // Logic to terminate contract
        emit Terminated(msg.sender, block.timestamp);
    }

    /// @notice Execute getContractState operation.
    function getContractState() public view returns (ContractState) {
        // Logic to determine current state
        return ContractState.Active;
    }

    /// @notice Receive ETH deposits.
    receive() external payable {}

    function pay() external payable onlyParties noReentrant {
        if (msg.value == 0) revert InsufficientPayment(0, msg.value);
        // Logic to handle payment
    }

    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}