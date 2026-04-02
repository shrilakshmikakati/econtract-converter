// ═══════════════════════════════════════════════════════════════
// Contract : ﻿Exhibit 2.1   Agreement And Plan Of Merger   Among   Merck Sharp & Dohme Corp.   Astros Merger Sub, Inc.   And   Acceleron Pharma Inc.   Dated As Of September 29, 2021
// Generated: 2026-04-02 10:49:36 UTC
// Tool     : eContract → Smart Contract Converter v1.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// ═══════════════════════════════════════════════════════════════
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

contract ContractName {
    // State variables
    address public owner;
    uint256 public paymentAmount;
    uint256 public deadline;
    bool public isPaymentReceived;
    bool public isTerminated;

    // Events
    event PaymentReceived(address indexed sender, uint256 amount);
    event TerminationConfirmed();

    // Modifiers
    modifier onlyOwner() {
        require(msg.sender == owner, "Not the contract owner");
        _;
    }

    modifier notTerminated() {
        require(!isTerminated, "Contract is terminated");
        _;
    }

    // Constructor
    constructor(uint256 _paymentAmount, uint256 _days) {
        owner = msg.sender;
        paymentAmount = _paymentAmount;
        deadline = block.timestamp + _days * 1 days;
    }

    // Functions

    /// @notice Function to receive payment
    function receivePayment() external payable notTerminated {
        require(msg.value == paymentAmount, "Incorrect payment amount");
        isPaymentReceived = true;
        emit PaymentReceived(msg.sender, msg.value);
    }

    /// @notice Function to terminate the contract
    function terminate() external onlyOwner notTerminated {
        isTerminated = true;
        emit TerminationConfirmed();
    }

    /// @notice Function to get current state of the contract
    function getContractState()
        public
        view
        returns (
            address,
            uint256,
            uint256,
            bool,
            bool
        )
    {
        return (owner, paymentAmount, deadline, isPaymentReceived, isTerminated);
    }

    /// @notice Accept ETH deposits.
    receive() external payable {}
}
