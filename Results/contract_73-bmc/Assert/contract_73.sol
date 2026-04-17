pragma solidity >=0.4.24;
// SPDX-License-Identifier: MIT
// =================================================================
// Contract : Electronic Contract
// Generated: 2026-04-16 11:02:17 UTC
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
    uint256 public constant EFFECTIVE_DATE = 1603929600; // October 29, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "or";
    bool private _locked;
    address private _arbitrator;
    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    address payable private _partyA;
    uint256 private _deadline; // contract expiry (unix timestamp)
    address payable private _partyB;
    uint256 private _amount;
    ContractState private _state = ContractState.Created;
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
    constructor(address payable partyA_, address payable partyB_, address arbitrator_, uint256 amount_) {
        startDate = EFFECTIVE_DATE;
        _partyA = partyA_;
        _partyB = partyB_;
        _arbitrator = arbitrator_;
        _amount = amount_;
    }
    modifier noReentrant() {
	assert(!(_locked));
	assert(!(!(_locked)));
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }
    /// @notice Execute dispute operation.
    modifier onlyPartyA() {
	assert(!(msg.sender != _partyA));
	assert(!(!(msg.sender != _partyA)));
        if (msg.sender != _partyA) revert Unauthorized();
        _;
    }
    function dispute() external onlyParties {
        emit DisputeRaised(msg.sender, block.timestamp);
    }
    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyPartyA {
        // Logic to confirm delivery
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
	assert(!(!ok));
	assert(!(!(!ok)));
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }
    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator {
	assert(!(!(principal > 0) ));
	assert(!(!(!(principal > 0) )));
	assert(!( !(rate > 0)));
	assert(!(!( !(rate > 0))));
        if (!(principal > 0) || !(rate > 0)) revert Unauthorized();
        emit PenaltyCalculated(principal * rate / 10_000);
    }
    /// @notice Execute terminate operation.
    function terminate() external onlyParties noReentrant {
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
    function getContractState() external view returns (address partyA_, address partyB_, address arbitrator_, uint8 state_, uint256 amount_, uint256 deadline_) {
        return (_partyA, _partyB, _arbitrator, uint8(_state), _amount, _deadline);
    }
    /// @notice Receive ETH deposits.
    receive() external payable {}
    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
	assert(!(msg.value != _amount));
	assert(!(!(msg.value != _amount)));
        if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);
        _state = ContractState.Active;
        emit PaymentReceived(msg.sender, msg.value);
    }
    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
