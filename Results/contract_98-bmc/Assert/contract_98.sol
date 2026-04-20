pragma solidity >=0.4.24;
// SPDX-License-Identifier: MIT
// =================================================================
// Contract : Article V Additional Agreements   Section 5.1         Preparation Of Proxy Statement/Prospectus; Stockholders Meeting.
// Generated: 2026-04-20 07:58:30 UTC
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
    uint256 public constant EFFECTIVE_DATE = 1624838400; // June 28, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "and";
    bool private _confidentialityAcknowledged;
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event DisputeRaised(address indexed initiator, uint256 timestamp);
    event MilestoneCompleted(address indexed caller, uint256 value);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    address private _arbitrator;
    ContractState private _state;
    bool private _locked;
    address payable private _partyA;
    address payable private _partyB;
    uint256 private _deadline; // contract expiry (unix timestamp)
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
    constructor(address arbitrator, address payable partyA_, address payable partyB_) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = arbitrator;
        _partyA = partyA_;
        _partyB = partyB_;
        _state = ContractState.Created;
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
	assert(!(_state != ContractState.Active));
	assert(!(!(_state != ContractState.Active)));
        if (_state != ContractState.Active) revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }
    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
	assert(!(_state != ContractState.Active));
	assert(!(!(_state != ContractState.Active)));
        if (_state != ContractState.Active) revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Completed;
        emit MilestoneCompleted(msg.sender, block.timestamp);
    }
    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
	assert(!(_state != ContractState.Disputed));
	assert(!(!(_state != ContractState.Disputed)));
        if (_state != ContractState.Disputed) revert InvalidState(uint8(_state), uint8(ContractState.Disputed));
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }
    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
	assert(!(_state == ContractState.Terminated));
	assert(!(!(_state == ContractState.Terminated)));
        if (_state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
        emit ContractTerminated(msg.sender, block.timestamp);
    }
    /// @notice Execute getContractState operation.
    function getContractState() external view returns (address partyA_, address partyB_, address arbitrator_, uint8 state_, uint256 amount_, uint256 deadline_) {
        return (_partyA, _partyB, _arbitrator, uint8(_state), 0, _deadline);
    }
    /// @notice Receive ETH deposits.
    receive() external payable {}
    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
	assert(!(msg.value == 0));
	assert(!(!(msg.value == 0)));
            if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        emit PaymentReceived(msg.sender, msg.value);
    }
    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
