// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Execution Version     Agreement And Plan Of Merger
// Generated: 2026-04-20 10:22:39 UTC
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
    uint256 public constant EFFECTIVE_DATE = 1620604800; // May 10, 2021
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "General";
    address private _arbitrator;
    bool private _locked;

    address payable private _partyA;
    uint256 private _deadline; // contract expiry (unix timestamp)
    ContractState private state;

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

    event DisputeRaised(address indexed party, uint256 timestamp);
    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PaymentReceived(address indexed from, uint256 amount);
    event PenaltyCalculated(uint256 penaltyWei);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);

    constructor(address parentParty_, address companyParty_, address arbitrator_) {
        startDate = EFFECTIVE_DATE;
        _partyA = payable(parentParty_);
        _partyB = payable(companyParty_);
        _arbitrator = arbitrator_;
        state = ContractState.Created;
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
        if (state == ContractState.Disputed) revert AlreadyDisputed();
        if (state == ContractState.Completed || state == ContractState.Terminated)
            revert InvalidState(uint8(state), uint8(ContractState.Active));
        state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    /// @notice Execute acknowledgeDelivery operation.
    function acknowledgeDelivery() external onlyParties {
        if (state != ContractState.Active) revert InvalidState(uint8(state), uint8(ContractState.Active));
        state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }

    /// @notice Execute calculatePenalty operation.
    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator {
        if (state != ContractState.Disputed)
            revert InvalidState(uint8(state), uint8(ContractState.Disputed));
        if (block.timestamp <= _deadline) return;
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(penalty);
    }

    /// @notice Execute terminate operation.
    function terminate() external onlyParties {
        if (state == ContractState.Terminated)
            revert InvalidState(uint8(state), uint8(ContractState.Active));
        state = ContractState.Terminated;
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
            if (!ok) revert InsufficientPayment(0, address(this).balance);
        }
        emit ContractTerminated(msg.sender, block.timestamp);
    }

    /// @notice Execute getContractState operation.
    function getContractState() external view returns (address partyA_, address partyB_, address arbitrator_, uint8 state_, uint256 amount_, uint256 deadline_) {
        return (_partyA, _partyB, _arbitrator, uint8(state), 0, _deadline);
    }

    /// @notice Receive ETH deposits.
    receive() external payable {        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Execute pay operation.
    function pay() external payable noReentrant {
        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);
        state = ContractState.Active;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Set the contract expiry deadline (seconds from now).
    function setDeadline(uint256 durationSeconds) external onlyArbitrator {
        _deadline = block.timestamp + durationSeconds;
    }
}
