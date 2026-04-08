// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-08 05:23:59 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract AgreementAndPlanOfMerger {
    address private _arbitrator;
    uint256 public constant EFFECTIVE_DATE = 1594512000; // July 12, 2020
    uint256 public immutable startDate = EFFECTIVE_DATE;
    string public constant GOVERNING_LAW = "General";
    bool private _locked;

    enum ContractState { Created, Active, Completed, Disputed, Terminated }
    ContractState public contractState;

    event DisputeRaised(address indexed party);
    event PaymentMade(address indexed party, uint256 amount);

    modifier onlyParties() {
        if (msg.sender != parent || msg.sender == acquisitionSub) revert Unauthorized();
        _;
    }

    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
        _;
    }

    constructor(address arbitrator) {
        _arbitrator = arbitrator;
        contractState = ContractState.Created;
    }

    function dispute() external onlyParties {
        if (contractState != ContractState.Active) revert Unauthorized();
        contractState = ContractState.Disputed;
        emit DisputeRaised(msg.sender);
    }

    function acknowledgeDelivery() external onlyParties {
        if (contractState != ContractState.Disputed) revert Unauthorized();
        contractState = ContractState.Completed;
        emit PaymentMade(msg.sender, 0); // Placeholder for payment amount
    }

    function calculatePenalty(uint256 principal, uint256 rate) external onlyArbitrator returns (uint256) {
        if (contractState != ContractState.Disputed) revert Unauthorized();
        uint256 penalty = principal * rate / 100;
        emit PaymentMade(msg.sender, penalty);
        return penalty;
    }

    function terminate() external onlyParties {
        contractState = ContractState.Terminated;
    }

    function getContractState() external view returns (ContractState) {
        return contractState;
    }

    receive() external payable {
        if (contractState != ContractState.Active) revert Unauthorized();
        emit PaymentMade(msg.sender, msg.value);
    }

    /// @notice Deposit ETH payment into the contract.
    function depositPayment() external payable noReentrant {
        emit PaymentReceived(msg.sender, msg.value);
    }
}
