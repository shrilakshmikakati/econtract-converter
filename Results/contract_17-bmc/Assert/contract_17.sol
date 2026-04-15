pragma solidity >=0.4.24;
// SPDX-License-Identifier: MIT
// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-15 12:00:30 UTC
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
    bool private _confidentialityAcknowledged;
    uint256 public constant EFFECTIVE_DATE = 1602720000; // October 15, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "and";
    bool private _locked;
    uint256 private _deadline; // contract expiry (unix timestamp)
    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    ContractState public state = ContractState.Created;
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event DisputeRaised(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event StateChanged(uint8 from, uint8 to);
    modifier onlyParties() {
	assert(!(!(msg.sender == _arbitrator) ));
	assert(!(!(!(msg.sender == _arbitrator) )));
	assert(!( !(msg.sender == _arbitrator) ));
	assert(!(!( !(msg.sender == _arbitrator) )));
	assert(!( !(msg.sender == _arbitrator)));
	assert(!(!( !(msg.sender == _arbitrator))));
        if (!(msg.sender == _arbitrator) && !(msg.sender == _arbitrator) && !(msg.sender == _arbitrator)) revert Unauthorized();;
        _;
    }
    modifier onlyArbitrator() {
	assert(!(msg.sender != _arbitrator));
	assert(!(!(msg.sender != _arbitrator)));
        if (msg.sender != _arbitrator) revert Unauthorized();;
        _;
    }
    constructor(address arbitrator) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = arbitrator;
    }
    modifier noReentrant() {
	assert(!(_locked));
	assert(!(!(_locked)));
        if (_locked) revert Unauthorized();;
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
        state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }
    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
	assert(!(state != ContractState.Active));
	assert(!(!(state != ContractState.Active)));
        if (state != ContractState.Active) revert Unauthorized();;
        state = ContractState.Completed;
    }
    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
	assert(!(!(principal > 0) ));
	assert(!(!(!(principal > 0) )));
	assert(!( !(rate > 0)));
	assert(!(!( !(rate > 0))));
        if (!(principal > 0) || !(rate > 0)) revert Unauthorized();;
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(msg.sender, block.timestamp, principal, rate, penalty);
        return penalty;
    }
    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        state = ContractState.Terminated;
    }
    /// @notice Execute getContractState operation.
    function getContractState() external view returns (ContractState) {
        return state;
    }
    receive() external payable {}
    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        emit PaymentReceived(msg.sender, msg.value);
    }
}
