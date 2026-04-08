// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Execution Version
// Generated: 2026-04-08 05:45:48 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract PascalCaseContract {
    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    uint256 public constant EFFECTIVE_DATE = 1598400000; // August 26, 2020
    uint256 public immutable startDate;
    string public constant GOVERNING_LAW = "Delaware";
    address private _arbitrator;
    bool private _locked;

    modifier onlyParties() {
        if (msg.sender != partyA || msg.sender == partyB) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    event DisputeRaised(address indexed party, string reason);
    event PaymentReceived(address indexed from, uint256 amount);
    event ContractCompleted();
    event ContractTerminated();
    event PenaltyCalculated(uint256 penaltyWei);

    address private partyA;
    address private partyB;

    constructor(address _partyA, address _partyB, address _arbitrator) {
        startDate = EFFECTIVE_DATE;
        _arbitrator = _arbitrator;
        partyA = _partyA;
        partyB = _partyB;
    }

    function dispute(string memory reason) external onlyParties {
        emit DisputeRaised(msg.sender, reason);
        // Additional logic for handling disputes
    }

    function acknowledgeDelivery() external onlyParties {
        if (contractState != ContractState.Active) revert Unauthorized();
        contractState = ContractState.Completed;
        emit ContractCompleted();
    }

    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
        uint256 penalty = principal * rate / 100;
        emit PenaltyCalculated(principal, rate, penalty);
        return penalty;
    }

    function terminate() external onlyParties {
        contractState = ContractState.Terminated;
        emit ContractTerminated();
    }

    function getContractState() external view returns (ContractState) {
        return contractState;
    }

    receive() external payable {
        if (contractState != ContractState.Active) revert Unauthorized();
        uint256 amount = msg.value;
        emit PaymentReceived(msg.sender, amount);
        // Additional logic for handling payments
    }

    modifier noReentrant() {
        if (_locked) revert Unauthorized();
        _locked = true;
        _;
        _locked = false;
    }

    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        emit PaymentReceived(msg.sender, msg.value);
    }
}
