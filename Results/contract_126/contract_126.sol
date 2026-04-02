// ═══════════════════════════════════════════════════════════════
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-02 10:26:03 UTC
// Tool     : eContract → Smart Contract Converter v1.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// ═══════════════════════════════════════════════════════════════
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

contract SalesforceMerger {
    // State variables
    address public parent;
    address public mergerSub1;
    address public mergerSub2;
    uint256 public deadline;
    bool public isTerminated;
    bool public isConfidentialityAcknowledged;
    bool public isIPAcknowledged;

    // Events
    event ConfidentialityAcknowledged(address indexed by);
    event IPAcknowledged(address indexed by);
    event TerminationInitiated(address indexed by);
    event DisputeStarted(address indexed by);

    modifier onlyParent() {
        require(msg.sender == parent, "Not authorized");
        _;
    }

    modifier onlyMergerSubs() {
        require(
            msg.sender == mergerSub1 || msg.sender == mergerSub2,
            "Not authorized"
        );
        _;
    }

    modifier notTerminated() {
        require(!isTerminated, "Contract is terminated");
        _;
    }

    constructor(address _parent, address _mergerSub1, address _mergerSub2) {
        parent = _parent;
        mergerSub1 = _mergerSub1;
        mergerSub2 = _mergerSub2;
        deadline = block.timestamp + 30 days; // 30 days from deployment
    }

    function acknowledgeConfidentiality() external onlyParent notTerminated {
        require(!isConfidentialityAcknowledged, "Confidentiality already acknowledged");
        isConfidentialityAcknowledged = true;
        emit ConfidentialityAcknowledged(parent);
    }

    function acknowledgeIP() external onlyParent notTerminated {
        require(!isIPAcknowledged, "IP already acknowledged");
        isIPAcknowledged = true;
        emit IPAcknowledged(parent);
    }

    function initiateTermination() external onlyParent notTerminated {
        isTerminated = true;
        emit TerminationInitiated(parent);
    }

    function dispute() external onlyMergerSubs notTerminated {
        emit DisputeStarted(msg.sender);
    }

    function getContractState()
        external
        view
        returns (
            address parent_,
            address mergerSub1_,
            address mergerSub2_,
            uint256 deadline_,
            bool isTerminated_,
            bool isConfidentialityAcknowledged_,
            bool isIPAcknowledged_
        )
    {
        return (
            parent,
            mergerSub1,
            mergerSub2,
            deadline,
            isTerminated,
            isConfidentialityAcknowledged,
            isIPAcknowledged
        );
    }

    function terminate() external onlyParent notTerminated {
        require(isConfidentialityAcknowledged && isIPAcknowledged, "Confidentiality and IP must be acknowledged");
        isTerminated = true;
        emit TerminationInitiated(parent);
    }
}
