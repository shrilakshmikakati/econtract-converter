"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.
"""

from __future__ import annotations

import json
from typing import Optional

from extractor import ContractDocument, ContractClause


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity 0.8.16 knowledge snippets injected into the system prompt
# ═══════════════════════════════════════════════════════════════════════════

SOLIDITY_RULES = """
MANDATORY SOLIDITY 0.8.16 RULES — follow every rule, no exceptions:

1. First line MUST be: // SPDX-License-Identifier: MIT
2. Second line MUST be: pragma solidity ^0.8.16;
3. Use `address payable` for addresses that receive ETH.
4. All ETH amounts in the contract are in WEI (1 ETH = 1e18 wei).
5. Use `block.timestamp` for time; deadlines are unix epoch seconds.
6. State variables: use `private` + getter unless external access required.
7. Use custom errors (revert CustomError()) instead of require strings to save gas.
8. Use `event` + `emit` for every state-changing operation.
9. Reentrancy guard: use a `bool private locked` flag on any ETH-sending function.
10. Use `unchecked` blocks ONLY for arithmetic that cannot overflow by design.
11. Mark view/pure functions correctly.
12. constructor must initialize all state, accept `address payable` where needed.
13. Implement a `receive()` external payable function if contract holds ETH.
14. Add NatSpec comments (/// @notice, /// @param, /// @return) on every function.
15. Avoid `tx.origin`; use `msg.sender` for authentication.
16. Never use `selfdestruct`.
17. Payable functions must validate msg.value == expected amount.
18. All deadlines must be set as: block.timestamp + (N * 1 days).
19. Implement `getContractState()` view function returning all key state fields.
20. Contract MUST compile with solc 0.8.16 without warnings.
"""

SOLIDITY_TEMPLATE_HINTS = """
STRUCTURAL TEMPLATE (adapt to contract specifics):

contract <Name>Contract {
    // ── Events ──────────────────────────────────────────────────────────
    event ContractCreated(address indexed partyA, address indexed partyB);
    event PaymentMade(address indexed payer, uint256 amount, uint256 timestamp);
    event ContractCompleted(uint256 timestamp);
    event ContractDisputed(address indexed initiator, string reason);

    // ── Custom Errors ────────────────────────────────────────────────────
    error Unauthorized();
    error InvalidState(uint8 current, uint8 required);
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);

    // ── State ────────────────────────────────────────────────────────────
    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    uint256 private _amount;
    uint256 private _deadline;
    bool private _locked;  // reentrancy guard

    // ── Modifiers ────────────────────────────────────────────────────────
    modifier onlyPartyA() { if (msg.sender != _partyA) revert Unauthorized(); _; }
    modifier onlyPartyB() { if (msg.sender != _partyB) revert Unauthorized(); _; }
    modifier inState(ContractState s) {
        if (_state != s) revert InvalidState(uint8(_state), uint8(s)); _;
    }
    modifier noReentrant() { require(!_locked); _locked = true; _; _locked = false; }
    modifier beforeDeadline() {
        if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp); _;
    }
}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  System prompt
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = f"""You are an expert Solidity 0.8.16 smart contract developer specializing in
digitizing legal electronic contracts into production-grade, gas-efficient, secure Solidity code.

{SOLIDITY_RULES}

OUTPUT FORMAT:
- Output ONLY valid Solidity source code.
- Do NOT include markdown fences (```solidity ... ```).
- Do NOT include any explanation before or after the code.
- The output must be a single complete .sol file that compiles cleanly.

{SOLIDITY_TEMPLATE_HINTS}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Clause → prose summary
# ═══════════════════════════════════════════════════════════════════════════

def _clause_summary(clause: ContractClause) -> str:
    parts = [f"  [{clause.index+1}] {clause.clause_type.upper()} — {clause.heading}"]
    # Trim raw text to 500 chars for prompt economy
    snippet = clause.raw_text[:500].replace("\n", " ")
    if len(clause.raw_text) > 500:
        snippet += "..."
    parts.append(f"      Text: {snippet}")
    if clause.amount_eth:
        parts.append(f"      Amount: {clause.amount_eth}")
    if clause.deadline_days is not None:
        parts.append(f"      Deadline: {clause.deadline_days} days")
    if clause.condition:
        parts.append(f"      Condition: {clause.condition}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
#  Public builders
# ═══════════════════════════════════════════════════════════════════════════

def build_user_prompt(doc: ContractDocument) -> str:
    """
    Build the user-turn prompt that describes the contract and requests
    a Solidity 0.8.16 implementation.
    """
    lines: list[str] = []

    lines.append("═" * 70)
    lines.append("ECONTRACT → SMART CONTRACT CONVERSION REQUEST")
    lines.append("═" * 70)
    lines.append("")

    # ── Contract metadata ────────────────────────────────────────────────
    lines.append(f"CONTRACT TITLE   : {doc.title}")
    lines.append(f"EFFECTIVE DATE   : {doc.effective_date or 'Not specified'}")
    lines.append(f"EXPIRY DATE      : {doc.expiry_date or 'Not specified'}")
    lines.append(f"GOVERNING LAW    : {doc.governing_law or 'Not specified'}")
    lines.append(f"CURRENCY         : {doc.currency}")
    lines.append("")

    # ── Parties ──────────────────────────────────────────────────────────
    lines.append("PARTIES:")
    for p in doc.parties:
        wallet = f"  [ETH address: {p.wallet_hint}]" if p.wallet_hint else ""
        lines.append(f"  - {p.role}: {p.name}{wallet}")
    lines.append("")

    # ── Clauses ──────────────────────────────────────────────────────────
    lines.append(f"CONTRACT CLAUSES ({len(doc.clauses)} total):")
    for clause in doc.clauses:
        lines.append(_clause_summary(clause))
        lines.append("")

    # ── Instructions ─────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append("INSTRUCTIONS:")
    lines.append("Convert the above eContract into a complete Solidity 0.8.16 smart contract.")
    lines.append("")
    lines.append("Requirements:")
    lines.append("1. Contract name: derive a clean PascalCase name from the contract title.")
    lines.append("2. Encode EVERY clause as on-chain logic, state, events, or modifiers.")
    lines.append("3. Payment clauses → payable functions with exact wei validation.")
    lines.append("4. Penalty clauses → automatic penalty deduction logic in wei.")
    lines.append("5. Expiry/term clauses → deadline as block.timestamp + days.")
    lines.append("6. Obligation clauses → state machine transitions + events.")
    lines.append("7. Dispute clauses → dispute() function + arbitrator address.")
    lines.append("8. Confidentiality / IP clauses → acknowledgement events + flags.")
    lines.append("9. Add a `getContractState()` view returning all key state variables as a tuple.")
    lines.append("10. Add a `terminate()` function accessible by both parties.")
    lines.append("11. Reentrancy guard on ALL ETH-transfer functions.")
    lines.append("12. NatSpec on every function and state variable.")
    lines.append("13. DO NOT use SafeMath — 0.8.x has built-in overflow protection.")
    lines.append("14. DO NOT import OpenZeppelin — produce a standalone contract.")
    lines.append("")
    lines.append("Now output ONLY the complete Solidity source code:")
    lines.append("═" * 70)

    return "\n".join(lines)


def get_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_validation_prompt(solidity_code: str, doc: ContractDocument) -> str:
    """
    Build a second-pass prompt that asks the LLM to self-review the
    generated contract against the original clauses.
    """
    clause_types = list({c.clause_type for c in doc.clauses})
    return f"""You are a Solidity 0.8.16 security auditor.

Review the following smart contract and verify:
1. All clause types ({', '.join(clause_types)}) are represented as on-chain logic.
2. No use of tx.origin, selfdestruct, or floating pragma.
3. Reentrancy guards present on ETH-sending functions.
4. Custom errors used (no require with strings).
5. All events emitted for state changes.
6. NatSpec comments present.

If ANY issue is found, output the COMPLETE corrected Solidity code only.
If the contract is correct, output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""
