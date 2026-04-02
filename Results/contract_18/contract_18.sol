// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Exhibit 2.1   Agreement And Plan Of Merger   By And Between   Bancorpsouth Bank   And   Cadence Bancorporation
// Generated: 2026-04-02 11:27:07 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract BancorpSouthCadenceMerger {
    // State Variables
    address public bancorpSouth;
    address public cadence;
    uint256 public effectiveTime;
    bool public isMerged;
    bool public isTerminated;
    address public arbitrator;

    // Events
    event Merged(address indexed by, address indexed to);
    event Terminated(address indexed by, address indexed to);

    // Modifiers
    modifier onlyBancorpSouth() {
        require(msg.sender == bancorpSouth, "Not BancorpSouth");
        _;
    }

    modifier onlyCadence() {
        require(msg.sender == cadence, "Not Cadence");
        _;
    }

    modifier onlyArbitrator() {
        require(msg.sender == arbitrator, "Not Arbitrator");
        _;
    }

    modifier notTerminated() {
        require(!isTerminated, "Contract is terminated");
        _;
    }

    // Constructor
    constructor(address _bancorpSouth, address _cadence, uint256 _effectiveTime, address _arbitrator) {
        bancorpSouth = _bancorpSouth;
        cadence = _cadence;
        effectiveTime = _effectiveTime;
        arbitrator = _arbitrator;
    }

    // Functions
    function merge() external onlyBancorpSouth notTerminated {
        require(block.timestamp >= effectiveTime, "Effective time has not yet arrived");
        isMerged = true;
        emit Merged(bancorpSouth, cadence);
    }

    function terminate() external onlyCadence notTerminated {
        isTerminated = true;
        emit Terminated(cadence, bancorpSouth);
    }

    function dispute() external onlyArbitrator notTerminated {
        // Logic for resolving disputes
    }

    function getContractState() public view returns (address, address, uint256, bool, bool, address) {
        return (bancorpSouth, cadence, effectiveTime, isMerged, isTerminated, arbitrator);
    }
}
