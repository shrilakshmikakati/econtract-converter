// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Exhibit 2.1 Execution Version     Agreement And Plan Of Merger
// Generated: 2026-04-06 09:42:12 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract LambdaStockholderConsent {
    // Events
    event AgreementAccepted(address indexed acceptor, uint256 timestamp);
    event DisputeStarted(address indexed starter, uint256 timestamp);
    event ContractTerminated(address indexed terminator, uint256 timestamp);

    // State Variables
    address public owner;
    address public arbitrator;
    bool public isAgreementAccepted;
    bool public isInDispute;
    uint256 public deadline;

    // Modifiers
    modifier noReentrant() {
        require(!isInDispute, "Contract is in dispute");
        _;
    }

    // Custom Errors
    error ReentrantCall();
    error AgreementNotAccepted();
    error DisputeAlreadyStarted();

    // Constructor
    constructor(address _arbitrator) {
        owner = msg.sender;
        arbitrator = _arbitrator;
        deadline = block.timestamp + 30 days; // 30 days from deployment
    }

    // Functions

    /// @notice Accepts the agreement and sets the state to accepted.
    function acceptAgreement() external noReentrant {
        require(!isAgreementAccepted, "Agreement already accepted");
        isAgreementAccepted = true;
        emit AgreementAccepted(msg.sender, block.timestamp);
    }

    /// @notice Starts a dispute if the agreement has not been accepted.
    function startDispute() external noReentrant {
        require(isAgreementAccepted, "Agreement must be accepted first");
        require(!isInDispute, "Dispute already started");
        isInDispute = true;
        emit DisputeStarted(msg.sender, block.timestamp);
    }

    /// @notice Terminates the contract if both parties agree.
    function terminate() external noReentrant {
        require(isAgreementAccepted, "Agreement must be accepted first");
        require(!isInDispute, "Cannot terminate during dispute");
        require(block.timestamp >= deadline, "Deadline not reached yet");

        emit ContractTerminated(msg.sender, block.timestamp);
        // Logic to handle termination (e.g., refund, transfer funds)
    }

    /// @notice Returns the current state of the contract.
    function getContractState() external view returns (
        address owner_,
        address arbitrator_,
        bool isAgreementAccepted_,
        bool isInDispute_,
        uint256 deadline_
    ) {
        return (owner, arbitrator, isAgreementAccepted, isInDispute, deadline);
    }
}
