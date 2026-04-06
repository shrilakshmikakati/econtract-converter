"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.

FIXES vs original (all are prompt-level fixes so the LLM generates correct
code in the first place — no post-processing patches needed):

  FIX 1 — SOLIDITY_RULES rule 9 (noReentrant):
    Original text was vague: "declare bool private _locked and apply
    modifier noReentrant()".  The LLM interpreted this as the modifier
    receiving _locked as a parameter → generated invalid
    `modifier noReentrant(bool storage _locked)`.
    Fix: rule now explicitly states NO PARAMETERS, reads state var directly,
    and shows the exact correct 4-line pattern inline.

  FIX 2 — SOLIDITY_TEMPLATE_HINTS noReentrant body:
    Template showed `require(!_locked, "reentrant call")` — a require() with
    a string, violating rule 7 (custom errors only). This directly taught the
    LLM to use a string-require inside the very modifier meant to be a best
    practice example.
    Fix: replaced with `if (_locked) revert ReentrantCall();` and added
    `error ReentrantCall();` to the custom errors block.

  FIX 3 — SOLIDITY_RULES rule 19 (getContractState return tuple):
    Original said "returning all key state fields as a tuple" with no
    restriction. The LLM included mapping(address => uint256) in the return
    list, which Solidity rejects at compile time.
    Fix: rule now explicitly states "NEVER include mapping types — return
    only value types (address, uint256, bool, enum cast to uint8, bytes32)".

  FIX 4 — build_validation_prompt:
    The self-review pass had no checks for the two compiler-breaking patterns
    above, so even the second LLM pass didn't catch them.
    Fix: added explicit checklist items 11 and 12 covering both patterns.
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
    NEVER write require(condition, "some string") — always use custom errors.
8.  Use `event` + `emit` for every state-changing operation.
9.  Reentrancy guard — EXACT PATTERN, copy precisely:
      bool private _locked;
      modifier noReentrant() {
          if (_locked) revert ReentrantCall();
          _locked = true;
          _;
          _locked = false;
      }
    CRITICAL: noReentrant() takes ZERO parameters. It reads _locked directly
    from contract storage. NEVER write modifier noReentrant(bool storage ...).
    Apply noReentrant() on EVERY function that transfers ETH.
10. Use `unchecked` blocks ONLY for arithmetic that cannot overflow by design.
11. Mark view/pure functions correctly.
12. constructor must initialize all state variables.
13. Implement `receive() external payable` if the contract holds ETH.
14. Add NatSpec (/// @notice, /// @param, /// @return) on EVERY function.
15. Never use `tx.origin`; use `msg.sender`.
16. Never use `selfdestruct`.
17. Payable functions must validate msg.value == expected amount.
18. All deadlines: block.timestamp + (N * 1 days).
19. Implement `getContractState()` as an external view function returning a
    tuple of scalar state fields.
    CRITICAL COMPILER RULE: mapping types CANNOT be returned from functions.
    Return ONLY value types: address, uint256, bool, bytes32, or enums cast
    to uint8. If you need to expose a mapping value, write a separate getter
    that takes a key parameter (e.g. function getBalance(address a) external
    view returns (uint256)).
20. Contract MUST compile with solc 0.8.16 without errors or warnings.
"""

# FIX 2: template noReentrant body uses custom error, not require string.
# FIX 1: modifier signature explicitly shows zero parameters.
SOLIDITY_TEMPLATE_HINTS = """
STRUCTURAL TEMPLATE (adapt to contract specifics):

contract <Name>Contract {

    // ── Custom Errors ────────────────────────────────────────────────────
    error Unauthorized();
    error InvalidState(uint8 current, uint8 required);
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error ReentrantCall();                          // ← required for noReentrant

    // ── Events ───────────────────────────────────────────────────────────
    event ContractCreated(address indexed partyA, address indexed partyB);
    event PaymentMade(address indexed payer, uint256 amount, uint256 timestamp);
    event ContractCompleted(uint256 timestamp);
    event ContractDisputed(address indexed initiator);

    // ── State ────────────────────────────────────────────────────────────
    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    uint256 private _amount;
    uint256 private _deadline;
    bool private _locked;    // ← reentrancy guard flag — NEVER pass as parameter

    // ── Modifiers ────────────────────────────────────────────────────────
    modifier onlyPartyA() {
        if (msg.sender != _partyA) revert Unauthorized();
        _;
    }
    modifier onlyPartyB() {
        if (msg.sender != _partyB) revert Unauthorized();
        _;
    }
    modifier inState(ContractState s) {
        if (_state != s) revert InvalidState(uint8(_state), uint8(s));
        _;
    }
    modifier beforeDeadline() {
        if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp);
        _;
    }

    // ── CORRECT noReentrant — ZERO parameters, reads _locked directly ────
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();   // custom error, NOT require string
        _locked = true;
        _;
        _locked = false;
    }

    // ── CORRECT getContractState — NO mapping in return tuple ────────────
    // WRONG:  returns (address, mapping(address => uint256), bool)   ← compiler error
    // CORRECT: returns (address, uint256, bool, uint8)               ← value types only
    function getContractState()
        external
        view
        returns (
            address partyA_,
            address partyB_,
            uint8   state_,       // enum cast to uint8
            uint256 amount_,
            uint256 deadline_,
            bool    locked_
        )
    {
        return (_partyA, _partyB, uint8(_state), _amount, _deadline, _locked);
    }

    // ── Separate getter for any mapping value ────────────────────────────
    // mapping(address => uint256) private _balances;
    // function getBalance(address account) external view returns (uint256) {
    //     return _balances[account];
    // }
}
"""

SYSTEM_PROMPT = f"""You are an expert Solidity 0.8.16 smart contract developer specialising in
converting legal electronic contracts into production-grade, gas-efficient, secure Solidity code.

{SOLIDITY_RULES}

OUTPUT FORMAT:
- Output ONLY valid Solidity source code.
- Do NOT include markdown fences (```solidity ... ```).
- Do NOT include any explanation before or after the code.
- The output must be a single complete .sol file that compiles cleanly with solc 0.8.16.

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
        "9.  Add getContractState() view returning scalar state vars as a tuple.",
        "    NEVER include mapping types in the return tuple — they cannot be",
        "    returned. Expose mapping values via separate key-parameter getters.",
        "10. Add terminate() accessible by both parties.",
        "11. Reentrancy guard: `bool private _locked` + `modifier noReentrant()`",
        "    with ZERO parameters. The modifier reads _locked from storage directly.",
        "    NEVER write modifier noReentrant(bool storage _locked).",
        "    Use `if (_locked) revert ReentrantCall();` NOT require() with a string.",
        "12. NatSpec on every function and state variable.",
        "13. Use custom errors — NO require() with string messages anywhere.",
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
    FIX 4: Added checklist items 11 and 12 to catch the two compiler-breaking
    patterns that previously slipped through the self-review pass.
    """
    clause_types = sorted({c.clause_type for c in doc.clauses})
    clean_title  = doc.title.lstrip("\ufeff").strip()
    return f"""You are a Solidity 0.8.16 security auditor reviewing an auto-generated contract.

Contract being audited: {clean_title}
Expected clause types : {', '.join(clause_types)}

Verify ALL of the following and fix any issues found:

1.  All clause types above are represented as on-chain logic (not just comments).
2.  No use of tx.origin, selfdestruct, or floating pragma.
3.  Reentrancy guard present: `bool private _locked` state variable declared.
4.  noReentrant modifier declared with ZERO parameters — correct form is:
        modifier noReentrant() {{
            if (_locked) revert ReentrantCall();
            _locked = true;
            _;
            _locked = false;
        }}
    If the modifier has any parameters (e.g. bool storage _locked) that is a
    COMPILER ERROR — remove the parameter and read _locked from storage directly.
5.  noReentrant applied to every function that transfers ETH.
6.  Custom errors used everywhere — ZERO require() calls with string arguments.
    Every require(cond, "msg") must be replaced with if (!cond) revert CustomError().
7.  Every state-changing function emits an event.
8.  NatSpec comments (/// @notice, /// @param) on every public/external function.
9.  SPDX-License-Identifier is the very first line of the file.
10. pragma solidity ^0.8.16 is the second line.
11. getContractState() return tuple contains NO mapping types.
    Mappings CANNOT be returned from Solidity functions — this is a compiler error.
    If a mapping appears in the return list, remove it and add a separate getter:
        function getBalance(address account) external view returns (uint256) {{
            return _balances[account];
        }}
12. Braces are balanced (no truncation). Count {{ and }} — they must be equal.
13. No OpenZeppelin imports, no SafeMath.

If ANY issue is found: output the COMPLETE corrected Solidity source only (no explanation).
If the contract passes all checks: output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""


# Alias for callers that prefer the "get_" naming style
get_validation_prompt = build_validation_prompt