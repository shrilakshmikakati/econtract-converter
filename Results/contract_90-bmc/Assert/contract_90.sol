pragma solidity >=0.4.24;
// SPDX-License-Identifier: MIT
// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-20 11:33:58 UTC
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
    address private _arbitrator;
    uint256 public constant EFFECTIVE_DATE = 1594512000; // July 12, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    bool private _locked;
    bool private _confidentialityAcknowledged;
    uint256 private _deadline;
    ContractState public contractState;
    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    address payable private _partyA;
    address payable private _partyB;
    modifier onlyParties() {
	assert(!(!(msg.sender == _partyA) ));
	assert(!(!(!(msg.sender == _partyA) )));
	assert(!( !(msg.sender == _partyB)));
	assert(!(!( !(msg.sender == _partyB))));
        if (!(msg.sender == _partyA) && !(msg.sender == _partyB)) revert Unauthorized();
        _;
    }
    modifier onlyArbitrator() {
	assert(!(msg.sender != _arbitrator));
	assert(!(!(msg.sender != _arbitrator)));
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event DisputeRaised(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    constructor(address payable _partyA_, address payable _partyB_, address _arbitrator_) {
        startDate = EFFECTIVE_DATE;
        _partyA = _partyA_;
        _partyB = _partyB_;
        _arbitrator = _arbitrator_;
        contractState = ContractState.Created;
    }
    modifier noReentrant() {
	assert(!(_locked));
	assert(!(!(_locked)));
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
	assert(!(contractState != ContractState.Active));
	assert(!(!(contractState != ContractState.Active)));
        if (contractState != ContractState.Active) revert InvalidState(uint8(contractState), uint8(ContractState.Active));
        contractState = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }
    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
	assert(!(contractState != ContractState.Active));
	assert(!(!(contractState != ContractState.Active)));
        if (contractState != ContractState.Active) revert InvalidState(uint8(contractState), uint8(ContractState.Active));
        contractState = ContractState.Completed;
        emit PaymentReceived(_partyB, address(this).balance);
    }
    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
	assert(!(!(principal > 0) ));
	assert(!(!(!(principal > 0) )));
	assert(!( !(rate > 0)));
	assert(!(!( !(rate > 0))));
        if (!(principal > 0) || !(rate > 0)) revert InsufficientPayment(0, 1);
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }
    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
	assert(!(contractState == ContractState.Terminated));
	assert(!(!(contractState == ContractState.Terminated)));
        if (contractState == ContractState.Terminated) revert InvalidState(uint8(contractState), uint8(ContractState.Active));
        contractState = ContractState.Terminated;
        emit PaymentReceived(_partyA, address(this).balance);
    }
    /// @notice Execute getContractState operation.
    function getContractState() external view returns (address partyA_, address partyB_, address arbitrator_, uint8 state_, uint256 amount_, uint256 deadline_) {
        return (_partyA, _partyB, _arbitrator, uint8(contractState), _amount, _deadline);
    }
    /// @notice Receive ETH deposits.
    receive() external payable {}
    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
	assert(!(msg.value != _amount));
	assert(!(!(msg.value != _amount)));
        if (msg.value != _amount) revert InsufficientPayment(msg.value, 0);
        emit PaymentReceived(msg.sender, msg.value);
    }
    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
