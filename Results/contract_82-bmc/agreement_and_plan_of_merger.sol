// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-06 10:06:09 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract MergerAgreement {
    // Custom errors
    error ReentrantCall();
    error InvalidPayment();
    error DeadlinePassed();
    error NotAuthorized();

    // State variables
    bool private _locked;
    address public parent;
    address public company;
    uint256 public deadline;
    bool public isTerminated;
    bool public confidentialityAcknowledged;

    // Events
    event PaymentReceived(uint256 amount);
    event PenaltyDeducted(uint256 penaltyAmount);
    event Terminated();
    event ConfidentialityAcknowledged();

    // Modifiers
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    modifier onlyAuthorized(address caller) {
        require(caller == parent || caller == company, "Not authorized");
        _;
    }

    // Constructor
    constructor(address _parent, address _company, uint256 daysUntilDeadline) {
        parent = _parent;
        company = _company;
        deadline = block.timestamp + (daysUntilDeadline * 1 days);
    }

    // Payment functions
    function pay(uint256 amount) external payable noReentrant onlyAuthorized(msg.sender) {
        if (msg.value != amount) revert InvalidPayment();
        emit PaymentReceived(amount);
    }

    // Penalty deduction logic
    function deductPenalty(uint256 penaltyAmount) external noReentrant onlyAuthorized(msg.sender) {
        require(block.timestamp < deadline, "Deadline passed");
        emit PenaltyDeducted(penaltyAmount);
    }

    // Termination function
    function terminate() external noReentrant onlyAuthorized(msg.sender) {
        isTerminated = true;
        emit Terminated();
    }

    // Confidentiality acknowledgement
    function acknowledgeConfidentiality() external noReentrant onlyAuthorized(msg.sender) {
        confidentialityAcknowledged = true;
        emit ConfidentialityAcknowledged();
    }

    // Get contract state
    function getContractState()
        external
        view
        returns (
            address,
            address,
            uint256,
            bool,
            bool
        )
    {
        return (parent, company, deadline, isTerminated, confidentialityAcknowledged);
    }

    /// @notice Accept ETH deposits.
    receive() external payable {}
}
