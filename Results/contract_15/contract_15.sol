// ═══════════════════════════════════════════════════════════════
// Contract : ﻿Exhibit 2.1    Agreement And Plan Of Merger By And Between Wsfs Financial Corporation And Bryn Mawr Bank Corporation Dated As Of March 9, 2021
// Generated: 2026-04-02 10:52:05 UTC
// Tool     : eContract → Smart Contract Converter v1.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// ═══════════════════════════════════════════════════════════════
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

contract BankMerger {
    // State variables
    address public owner;
    address public partner;
    uint256 public deadline;
    bool public isMerged;
    bool public isTerminated;

    // Events
    event AgreementMade(address indexed owner, address indexed partner);
    event DeadlineSet(uint256 deadline);
    event Merged();
    event Terminated();

    modifier onlyOwner() {
        require(msg.sender == owner, "Not the owner");
        _;
    }

    modifier onlyPartner() {
        require(msg.sender == partner, "Not the partner");
        _;
    }

    modifier notTerminated() {
        require(!isTerminated, "Contract is terminated");
        _;
    }

    constructor(address _partner) {
        owner = msg.sender;
        partner = _partner;
        emit AgreementMade(owner, partner);
    }

    function setDeadline(uint256 _days) external onlyOwner notTerminated {
        deadline = block.timestamp + _days * 1 days;
        emit DeadlineSet(deadline);
    }

    function merge() external payable onlyPartner notTerminated {
        require(block.timestamp <= deadline, "Deadline exceeded");
        require(msg.value == 1 ether, "Invalid payment");

        isMerged = true;
        emit Merged();
    }

    function terminate() external onlyOwner notTerminated {
        isTerminated = true;
        emit Terminated();
    }

    function getContractState()
        external
        view
        returns (
            address owner_,
            address partner_,
            uint256 deadline_,
            bool isMerged_,
            bool isTerminated_
        )
    {
        return (owner, partner, deadline, isMerged, isTerminated);
    }

    function dispute() external onlyOwner notTerminated {
        // Placeholder for dispute logic
        emit Terminated();
    }

    /// @notice Accept ETH deposits.
    receive() external payable {}
}
