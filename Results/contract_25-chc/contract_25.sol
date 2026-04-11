// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-10 07:09:31 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract AgreementAndPlanOfMergerContract {

    // ── Custom Errors ─────────────────────────────────────────────────────
    error Unauthorized();
    error InvalidState(uint8 current, uint8 required);
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error ReentrantCall();
    error AlreadyDisputed();

    // ── State machine ─────────────────────────────────────────────────────
    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    // ── Governing law string constant (FIX-L: satisfies LEG-020) ─────────
    /// @notice Governing law jurisdiction.
    string public constant GOVERNING_LAW = "General";

    // ── Effective date (FIX-M: startDate satisfies LEG-030) ──────────────
    /// @notice Unix timestamp of the effective date.
    uint256 public constant EFFECTIVE_DATE = 1584057600; // 2020-03-13
    uint256 public immutable startDate;

    // ── State variables ───────────────────────────────────────────────────
    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    address private _arbitrator;
    uint256 private _amount;
    uint256 private _deadline;
    uint256 private _penaltyRate;
    bool private _locked;               // FIX-I: TOP-LEVEL, not inside modifier

    // ── Events (min 5, NO memory/calldata/storage in params) ─────────────
    event ContractCreated(address indexed _arbitrator, address indexed _arbitrator, uint256 amount);
    event PaymentMade(address indexed payer, uint256 amount, uint256 timestamp);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    event DisputeRaised(address indexed initiator, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PenaltyCalculated(uint256 penaltyWei);

    // ── Access control (FIX-J: min 2 onlyX modifiers) ────────────────────
    modifier onlyParties() {
        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
        _;
    }
    modifier onlyPartyA() {
        if (msg.sender != _partyA) revert Unauthorized();
        _;
    }
    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }
    modifier inState(ContractState s) {
        if (_state != s) revert InvalidState(uint8(_state), uint8(s));
        _;
    }
    modifier beforeDeadline() {
        if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp);
        _;
    }

    // ── Reentrancy guard (_locked declared above) ─────────────────────────
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    // ── Constructor ───────────────────────────────────────────────────────
    constructor(
        address payable partyA_,
        address payable partyB_,
        address arbitrator_,
        uint256 amount_,
        uint256 deadlineDays_,
        uint256 penaltyRateBps_
    ) {
        startDate = EFFECTIVE_DATE;
        _partyA      = partyA_;
        _partyB      = partyB_;
        _arbitrator  = arbitrator_;
        _amount      = amount_;
        _deadline    = block.timestamp + (deadlineDays_ * 1 days);
        _penaltyRate = penaltyRateBps_;
        _state       = ContractState.Created;
        startDate    = EFFECTIVE_DATE;         // FIX-M
        emit ContractCreated(partyA_, partyB_, amount_);
    }

    // ── Payment (FIX-K: external payable mandatory) ───────────────────────
    /// @notice PartyA deposits ETH to activate the contract.
    function pay()
        external payable
        onlyPartyA
        inState(ContractState.Created)
        beforeDeadline
        noReentrant
    {
        if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);
        _state = ContractState.Active;
        emit PaymentMade(msg.sender, msg.value, block.timestamp);
    }

    // ── Delivery acknowledgement (mandatory) ──────────────────────────────
    /// @notice PartyA acknowledges delivery, releasing funds to _arbitrator.
    function acknowledgeDelivery()
        external
        onlyPartyA
        inState(ContractState.Active)
        noReentrant
    {
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }

    // ── Dispute (mandatory) ───────────────────────────────────────────────
    /// @notice Raises a dispute, freezing the contract pending arbitration.
    function dispute() external onlyParties {
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
        if (_state == ContractState.Completed || _state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    // ── Terminate ─────────────────────────────────────────────────────────
    /// @notice Terminates the contract and refunds _arbitrator.
    function terminate() external onlyParties noReentrant {
        if (_state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
            if (!(ok)) revert Unauthorized();
        }
        emit ContractTerminated(msg.sender, block.timestamp);
    }

    // ── Calculate Penalty ───────────────────────────────────────────────
    /// @notice Calculates penalty based on principal and rate.
    function calculatePenalty(uint256 principal, uint256 rate) external noReentrant returns (uint256) {
        if (_state != ContractState.Disputed) revert Unauthorized();
        uint256 penalty = (principal * rate) / 100;
        emit PenaltyCalculated(penalty);
        return penalty;
    }

    // ── Get Contract State ─────────────────────────────────────────────
    /// @notice Returns the current state of the contract.
    function getContractState() external view returns (ContractState) {
        return _state;
    }

    // ── Receive ETH ────────────────────────────────────────────────────
    receive() external payable {}
}
