// SPDX-License-Identifier: MIT
pragma solidity ^0.8.16;

// =================================================================
// Contract : Agreement And Plan Of Merger
// Generated: 2026-04-02 11:15:27 UTC
// Tool     : eContract -> Smart Contract Converter v2.0
// Solidity : 0.8.16
// WARNING  : Review thoroughly before deployment on mainnet.
// =================================================================

contract CaiInternationalMitsubishiHcCapitalCattleyaAcquisition {

    // State Variables
    address public parent;
    address public mergerSub;
    address public company;
    uint256 public terminationFee;
    uint256 public parentTerminationFee;
    bool public isTerminated;

    enum ContractState { Created, Active, Terminated }
    ContractState public state;

    event AgreementSigned(address indexed by, string role);
    event TerminationFeePaid(address indexed by, uint256 amount);
    event ParentTerminationFeePaid(address indexed by, uint256 amount);

    modifier noReentrant() {
        require(!isTerminated, "Contract is terminated");
        _;
    }

    constructor(uint256 _terminationFee, uint256 _parentTerminationFee) {
        parent = msg.sender;
        mergerSub = msg.sender; // Placeholder for actual Merger Sub address
        company = msg.sender; // Placeholder for actual Company address
        terminationFee = _terminationFee;
        parentTerminationFee = _parentTerminationFee;
        state = ContractState.Created;
    }

    function signAgreement(string memory role) external noReentrant {
        require(msg.sender == parent || msg.sender == mergerSub || msg.sender == company, "Unauthorized");
        emit AgreementSigned(msg.sender, role);
    }

    function terminate() external noReentrant {
        if (msg.sender == parent) {
            state = ContractState.Terminated;
            emit TerminationFeePaid(parent, terminationFee);
        } else if (msg.sender == mergerSub || msg.sender == company) {
            state = ContractState.Terminated;
            emit ParentTerminationFeePaid(msg.sender, parentTerminationFee);
        }
    }

    function getContractState() external view returns (address, address, address, uint256, uint256, bool, ContractState) {
        return (parent, mergerSub, company, terminationFee, parentTerminationFee, isTerminated, state);
    }
}
