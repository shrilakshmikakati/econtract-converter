pragma solidity >=0.4.24;
// SPDX-License-Identifier: MIT
// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-20 11:11:22 UTC
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
    uint256 public constant EFFECTIVE_DATE = 1624406400; // June 23, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "and";
    bool private _locked;
    bool private _confidentialityAcknowledged;
    uint256 private _deadline;
    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    modifier onlyParties() {
	assert(!(msg.sender != _partyA ));
	assert(!(!(msg.sender != _partyA )));
	assert(!( msg.sender != _partyB));
	assert(!(!( msg.sender != _partyB)));
        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
        _;
    }
    modifier onlyArbitrator() {
	assert(!(msg.sender != _arbitrator));
	assert(!(!(msg.sender != _arbitrator)));
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }
    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    constructor(address payable _partyA_, address payable _partyB_, address arbitrator_) {
        _partyA = _partyA_;
        _partyB = _partyB_;
        _arbitrator = arbitrator_;
        startDate = EFFECTIVE_DATE;
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
    modifier onlyPartyA() {
	assert(!(msg.sender != _partyA));
	assert(!(!(msg.sender != _partyA)));
        if (msg.sender != _partyA) revert Unauthorized();
        _;
    }
    /// @notice Execute acknowledgeNonDisclosure operation.
    function acknowledgeNonDisclosure() external onlyParties {
        _confidentialityAcknowledged = true;
        emit NonDisclosureAcknowledged(msg.sender, block.timestamp);
    }
    /// @notice Execute dispute operation.
    function dispute() external onlyArbitrator {
	assert(!(_state == ContractState.Disputed));
	assert(!(!(_state == ContractState.Disputed)));
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
	assert(!(_state == ContractState.Completed ));
	assert(!(!(_state == ContractState.Completed )));
	assert(!( _state == ContractState.Terminated));
	assert(!(!( _state == ContractState.Terminated)));
        if (_state == ContractState.Completed || _state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }
    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyPartyA {
	assert(!(_state != ContractState.Active));
	assert(!(!(_state != ContractState.Active)));
        if (_state != ContractState.Active) revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
	assert(!(!ok));
	assert(!(!(!ok)));
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }
    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
	assert(!(_state != ContractState.Disputed));
	assert(!(!(_state != ContractState.Disputed)));
        if (_state != ContractState.Disputed)
            revert InvalidState(uint8(_state), uint8(ContractState.Disputed));
	assert(!(block.timestamp <= _deadline));
	assert(!(!(block.timestamp <= _deadline)));
        if (block.timestamp <= _deadline) return 0;
        uint256 daysLate = (block.timestamp - _deadline) / 1 days;
        uint256 penaltyWei = (principal * rate * daysLate) / 10_000;
        emit PenaltyCalculated(penaltyWei);
        return penaltyWei;
    }
    /// @notice Execute terminate operation.
    function terminate() external onlyParties noReentrant {
	assert(!(_state == ContractState.Terminated));
	assert(!(!(_state == ContractState.Terminated)));
        if (_state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
	assert(!(address(this).balance > 0));
	assert(!(!(address(this).balance > 0)));
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
	assert(!(!ok));
	assert(!(!(!ok)));
            if (!ok) revert InsufficientPayment(0, address(this).balance);
        }
        emit ContractTerminated(msg.sender, block.timestamp);
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
        return (_partyA, _partyB, _arbitrator, uint8(_state), 0, _deadline);
    }
    /// @notice Receive ETH deposits.
    receive() external payable {        emit PaymentReceived(msg.sender, msg.value);
    }
    /// @notice Execute depositPayment operation.
    function depositPayment() external payable noReentrant {
	assert(!(msg.value == 0));
	assert(!(!(msg.value == 0)));
        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        emit PaymentReceived(msg.sender, msg.value);
    }
}
