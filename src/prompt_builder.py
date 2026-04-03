
"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.

FIXES vs original:
  1. build_validation_prompt was defined but never exported/documented for use.
     Added get_validation_prompt() as a clean alias and updated module docstring
     so callers know to use it as a second LLM pass.
"""

from __future__ import annotations

from extractor import ContractDocument, ContractClause


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity rules injected into the system prompt
# ═══════════════════════════════════════════════════════════════════════════

SOLIDITY_RULES = """
MANDATORY SOLIDITY 0.8.16 RULES — follow every rule, no exceptions:

1.  First line MUST be: // SPDX-License-Identifier: MIT
2.  Second line MUST be: pragma solidity ^0.8.16;
3.  Use `address payable` for addresses that receive ETH.
4.  All ETH amounts in the contract are in WEI (1 ETH = 1e18 wei).
5.  Use `block.timestamp` for time; deadlines are unix epoch seconds.
6.  State variables: use `private` + getter unless external access required.
7.  Use custom errors (revert CustomError()) instead of require strings.
8.  Use `event` + `emit` for every state-changing operation.
9.  Reentrancy guard: declare `bool private _locked` and apply a
    `modifier noReentrant()` on EVERY function that transfers ETH.
10. Use `unchecked` blocks ONLY for arithmetic that cannot overflow by design.
11. Mark view/pure functions correctly.
12. constructor must initialize all state variables.
13. Implement `receive() external payable` if the contract holds ETH.
14. Add NatSpec (/// @notice, /// @param, /// @return) on EVERY function.
15. Never use `tx.origin`; use `msg.sender`.
16. Never use `selfdestruct`.
17. Payable functions must validate msg.value == expected amount.
18. All deadlines: block.timestamp + (N * 1 days).
19. Implement `getContractState()` returning all key state fields as a tuple.
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
    bool private _locked;    // ← reentrancy guard flag (MANDATORY)

    // ── Modifiers ────────────────────────────────────────────────────────
    modifier onlyPartyA() { if (msg.sender != _partyA) revert Unauthorized(); _; }
    modifier onlyPartyB() { if (msg.sender != _partyB) revert Unauthorized(); _; }
    modifier inState(ContractState s) {
        if (_state != s) revert InvalidState(uint8(_state), uint8(s)); _;
    }
    modifier noReentrant() {
        require(!_locked, "reentrant call");
        _locked = true;
        _;
        _locked = false;
    }
    modifier beforeDeadline() {
        if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp); _;
    }
}
"""

SYSTEM_PROMPT = f"""You are an expert Solidity 0.8.16 smart contract developer specialising in
converting legal electronic contracts into production-grade, gas-efficient, secure Solidity code.

{SOLIDITY_RULES}

OUTPUT FORMAT:
- Output ONLY valid Solidity source code.
- Do NOT include markdown fences (```solidity ... ```).
- Do NOT include any explanation before or after the code.
- The output must be a single complete .sol file that compiles cleanly.

{SOLIDITY_TEMPLATE_HINTS}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Clause → prose summary (truncated for prompt economy)
# ═══════════════════════════════════════════════════════════════════════════

def _clause_summary(clause: ContractClause) -> str:
    parts = [f"  [{clause.index+1}] {clause.clause_type.upper()} — {clause.heading}"]
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
    """Build the user-turn prompt that requests a Solidity 0.8.16 implementation."""
    lines: list[str] = []

    sep = "=" * 70
    lines += [sep, "ECONTRACT -> SMART CONTRACT CONVERSION REQUEST", sep, ""]

    clean_title = doc.title.lstrip("\ufeff").strip()
    lines += [
        f"CONTRACT TITLE   : {clean_title}",
        f"EFFECTIVE DATE   : {doc.effective_date or 'Not specified'}",
        f"EXPIRY DATE      : {doc.expiry_date or 'Not specified'}",
        f"GOVERNING LAW    : {doc.governing_law or 'Not specified'}",
        f"CURRENCY         : {doc.currency}",
        "",
    ]

    lines.append("PARTIES:")
    for p in doc.parties:
        wallet = f"  [ETH: {p.wallet_hint}]" if p.wallet_hint else ""
        lines.append(f"  - {p.role}: {p.name}{wallet}")
    lines.append("")

    lines.append(f"CONTRACT CLAUSES ({len(doc.clauses)} total):")
    for clause in doc.clauses:
        lines.append(_clause_summary(clause))
        lines.append("")

    lines += [
        sep,
        "INSTRUCTIONS:",
        "Convert the above eContract into a complete Solidity 0.8.16 smart contract.",
        "",
        "Requirements:",
        "1.  Contract name: derive a clean PascalCase name from the contract title.",
        "2.  Encode EVERY clause as on-chain logic, state, events, or modifiers.",
        "3.  Payment clauses    -> payable functions with exact wei validation.",
        "4.  Penalty clauses    -> automatic penalty deduction logic in wei.",
        "5.  Expiry/term clauses -> deadline as block.timestamp + N days.",
        "6.  Obligation clauses -> state machine transitions + events.",
        "7.  Dispute clauses    -> dispute() function + arbitrator address.",
        "8.  Confidentiality/IP -> acknowledgement events + bool flags.",
        "9.  Add getContractState() view returning all key state vars as a tuple.",
        "10. Add terminate() accessible by both parties.",
        "11. Reentrancy guard (bool _locked + noReentrant modifier) on ALL ETH-transfer functions.",
        "12. NatSpec on every function and state variable.",
        "13. Use custom errors — NO require() with string messages.",
        "14. DO NOT use SafeMath — 0.8.x has built-in overflow protection.",
        "15. DO NOT import OpenZeppelin — standalone contract only.",
        "",
        "Now output ONLY the complete Solidity source code:",
        sep,
    ]

    return "\n".join(lines)


def get_system_prompt() -> str:
    """Return the system prompt for the generation pass."""
    return SYSTEM_PROMPT


def build_validation_prompt(solidity_code: str, doc: ContractDocument) -> str:
    """
    Build a self-review / audit prompt for a second LLM pass.

    Wire this in after the initial generation:
        raw2 = llm.generate(system, build_validation_prompt(code, doc))
        code = extract_solidity(raw2)

    The model is instructed to return the COMPLETE corrected code if it finds
    issues, or the EXACT same code if everything is fine.
    """
    clause_types = sorted({c.clause_type for c in doc.clauses})
    clean_title  = doc.title.lstrip("\ufeff").strip()
    return f"""You are a Solidity 0.8.16 security auditor reviewing an auto-generated contract.

Contract being audited: {clean_title}
Expected clause types : {', '.join(clause_types)}

Verify ALL of the following:
1. All clause types above are represented as on-chain logic (not just comments).
2. No use of tx.origin, selfdestruct, or floating pragma.
3. Reentrancy guard (bool _locked + noReentrant modifier) present on every ETH-transfer function.
4. Custom errors used everywhere — no require() with string arguments.
5. Every state-changing function emits an event.
6. NatSpec comments (/// @notice, /// @param) present on every public/external function.
7. SPDX-License-Identifier is the very first line of the file.
8. pragma solidity ^0.8.16 is the second line.
9. Braces are balanced (no truncation).
10. No OpenZeppelin imports, no SafeMath.

If ANY issue is found: output the COMPLETE corrected Solidity source only (no explanation).
If the contract passes all checks: output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""


# Alias for callers that prefer the "get_" naming style
"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.

FIXES vs original:
  1. build_validation_prompt was defined but never exported/documented for use.
     Added get_validation_prompt() as a clean alias and updated module docstring
     so callers know to use it as a second LLM pass.
"""

from __future__ import annotations

from extractor import ContractDocument, ContractClause


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity rules injected into the system prompt
# ═══════════════════════════════════════════════════════════════════════════

SOLIDITY_RULES = """
MANDATORY SOLIDITY 0.8.16 RULES — follow every rule, no exceptions:

1.  First line MUST be: // SPDX-License-Identifier: MIT
2.  Second line MUST be: pragma solidity ^0.8.16;
3.  Use `address payable` for addresses that receive ETH.
4.  All ETH amounts in the contract are in WEI (1 ETH = 1e18 wei).
5.  Use `block.timestamp` for time; deadlines are unix epoch seconds.
6.  State variables: use `private` + getter unless external access required.
7.  Use custom errors (revert CustomError()) instead of require strings.
8.  Use `event` + `emit` for every state-changing operation.
9.  Reentrancy guard: declare `bool private _locked` and apply a
    `modifier noReentrant()` on EVERY function that transfers ETH.
10. Use `unchecked` blocks ONLY for arithmetic that cannot overflow by design.
11. Mark view/pure functions correctly.
12. constructor must initialize all state variables.
13. Implement `receive() external payable` if the contract holds ETH.
14. Add NatSpec (/// @notice, /// @param, /// @return) on EVERY function.
15. Never use `tx.origin`; use `msg.sender`.
16. Never use `selfdestruct`.
17. Payable functions must validate msg.value == expected amount.
18. All deadlines: block.timestamp + (N * 1 days).
19. Implement `getContractState()` returning all key state fields as a tuple.
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
    bool private _locked;    // ← reentrancy guard flag (MANDATORY)

    // ── Modifiers ────────────────────────────────────────────────────────
    modifier onlyPartyA() { if (msg.sender != _partyA) revert Unauthorized(); _; }
    modifier onlyPartyB() { if (msg.sender != _partyB) revert Unauthorized(); _; }
    modifier inState(ContractState s) {
        if (_state != s) revert InvalidState(uint8(_state), uint8(s)); _;
    }
    modifier noReentrant() {
        require(!_locked, "reentrant call");
        _locked = true;
        _;
        _locked = false;
    }
    modifier beforeDeadline() {
        if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp); _;
    }
}
"""

SYSTEM_PROMPT = f"""You are an expert Solidity 0.8.16 smart contract developer specialising in
converting legal electronic contracts into production-grade, gas-efficient, secure Solidity code.

{SOLIDITY_RULES}

OUTPUT FORMAT:
- Output ONLY valid Solidity source code.
- Do NOT include markdown fences (```solidity ... ```).
- Do NOT include any explanation before or after the code.
- The output must be a single complete .sol file that compiles cleanly.

{SOLIDITY_TEMPLATE_HINTS}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Clause → prose summary (truncated for prompt economy)
# ═══════════════════════════════════════════════════════════════════════════

def _clause_summary(clause: ContractClause) -> str:
    parts = [f"  [{clause.index+1}] {clause.clause_type.upper()} — {clause.heading}"]
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
    """Build the user-turn prompt that requests a Solidity 0.8.16 implementation."""
    lines: list[str] = []

    sep = "=" * 70
    lines += [sep, "ECONTRACT -> SMART CONTRACT CONVERSION REQUEST", sep, ""]

    clean_title = doc.title.lstrip("\ufeff").strip()
    lines += [
        f"CONTRACT TITLE   : {clean_title}",
        f"EFFECTIVE DATE   : {doc.effective_date or 'Not specified'}",
        f"EXPIRY DATE      : {doc.expiry_date or 'Not specified'}",
        f"GOVERNING LAW    : {doc.governing_law or 'Not specified'}",
        f"CURRENCY         : {doc.currency}",
        "",
    ]

    lines.append("PARTIES:")
    for p in doc.parties:
        wallet = f"  [ETH: {p.wallet_hint}]" if p.wallet_hint else ""
        lines.append(f"  - {p.role}: {p.name}{wallet}")
    lines.append("")

    lines.append(f"CONTRACT CLAUSES ({len(doc.clauses)} total):")
    for clause in doc.clauses:
        lines.append(_clause_summary(clause))
        lines.append("")

    lines += [
        sep,
        "INSTRUCTIONS:",
        "Convert the above eContract into a complete Solidity 0.8.16 smart contract.",
        "",
        "Requirements:",
        "1.  Contract name: derive a clean PascalCase name from the contract title.",
        "2.  Encode EVERY clause as on-chain logic, state, events, or modifiers.",
        "3.  Payment clauses    -> payable functions with exact wei validation.",
        "4.  Penalty clauses    -> automatic penalty deduction logic in wei.",
        "5.  Expiry/term clauses -> deadline as block.timestamp + N days.",
        "6.  Obligation clauses -> state machine transitions + events.",
        "7.  Dispute clauses    -> dispute() function + arbitrator address.",
        "8.  Confidentiality/IP -> acknowledgement events + bool flags.",
        "9.  Add getContractState() view returning all key state vars as a tuple.",
        "10. Add terminate() accessible by both parties.",
        "11. Reentrancy guard (bool _locked + noReentrant modifier) on ALL ETH-transfer functions.",
        "12. NatSpec on every function and state variable.",
        "13. Use custom errors — NO require() with string messages.",
        "14. DO NOT use SafeMath — 0.8.x has built-in overflow protection.",
        "15. DO NOT import OpenZeppelin — standalone contract only.",
        "",
        "Now output ONLY the complete Solidity source code:",
        sep,
    ]

    return "\n".join(lines)


def get_system_prompt() -> str:
    """Return the system prompt for the generation pass."""
    return SYSTEM_PROMPT


def build_validation_prompt(solidity_code: str, doc: ContractDocument) -> str:
    """
    Build a self-review / audit prompt for a second LLM pass.

    Wire this in after the initial generation:
        raw2 = llm.generate(system, build_validation_prompt(code, doc))
        code = extract_solidity(raw2)

    The model is instructed to return the COMPLETE corrected code if it finds
    issues, or the EXACT same code if everything is fine.
    """
    clause_types = sorted({c.clause_type for c in doc.clauses})
    clean_title  = doc.title.lstrip("\ufeff").strip()
    return f"""You are a Solidity 0.8.16 security auditor reviewing an auto-generated contract.

Contract being audited: {clean_title}
Expected clause types : {', '.join(clause_types)}

Verify ALL of the following:
1. All clause types above are represented as on-chain logic (not just comments).
2. No use of tx.origin, selfdestruct, or floating pragma.
3. Reentrancy guard (bool _locked + noReentrant modifier) present on every ETH-transfer function.
4. Custom errors used everywhere — no require() with string arguments.
5. Every state-changing function emits an event.
6. NatSpec comments (/// @notice, /// @param) present on every public/external function.
7. SPDX-License-Identifier is the very first line of the file.
8. pragma solidity ^0.8.16 is the second line.
9. Braces are balanced (no truncation).
10. No OpenZeppelin imports, no SafeMath.

If ANY issue is found: output the COMPLETE corrected Solidity source only (no explanation).
If the contract passes all checks: output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""


# Alias for callers that prefer the "get_" naming style
get_validation_prompt = build_validation_prompt