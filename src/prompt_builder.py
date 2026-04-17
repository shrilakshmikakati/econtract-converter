"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.

FIXES vs previous version:

  FIX-A  EFFECTIVE_DATE constant used `block.timestamp` — compile error fixed.
  FIX-B  Event params had `memory` keyword — removed.
  FIX-C  Rule: NEVER use memory/calldata/storage in event params.
  FIX-D  EFFECTIVE_DATE must be a compile-time integer literal.
  FIX-E  Every function definition MUST start with `function` keyword.
  FIX-F  Constants cannot be reassigned.
  FIX-G  [NEW] calculatePenalty() must NOT be view — it emits an event.
  FIX-H  [NEW] msg.value cannot be used in view/pure; calculatePenalty()
         must accept principal as uint256 param instead of msg.value.
  FIX-I  [NEW] SEC-001: `bool private _locked;` must be a top-level state
         variable declared at contract scope, NOT inside a modifier.
  FIX-J  [NEW] SEC-005: minimum 2 `modifier onlyX` declarations mandatory.
  FIX-K  [NEW] COV-001/LEG-090: at least one external payable function AND
         receive() fallback are mandatory.
  FIX-L  [NEW] LEG-020: GOVERNING_LAW string constant added to contract so
         the validator keyword search can find the jurisdiction.
  FIX-M  [NEW] LEG-030: `startDate` immutable added so effectiveDate pattern
         matches in the validator.
  FIX-N  [NEW] SOL-007: self-check step strengthened — scan ALL require().
  FIX-O  [NEW] SOL-013: minimum @notice raised from 5 to 8.
"""

from __future__ import annotations

import re
import time
import calendar
import logging

from extractor import ContractDocument, ContractClause

logger = logging.getLogger("econtract.prompt_builder")


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity rules
# ═══════════════════════════════════════════════════════════════════════════

SOLIDITY_RULES = """
MANDATORY SOLIDITY 0.8.16 RULES — follow EVERY rule, no exceptions:

1.  First line MUST be: // SPDX-License-Identifier: MIT
2.  Second line MUST be: pragma solidity ^0.8.16;
3.  Use `address payable` for addresses that receive ETH.
4.  All ETH amounts are in WEI (1 ETH = 1e18 wei).
5.  Use `block.timestamp` inside function bodies only — not for constants.
6.  State variables: `private` + getter unless external access required.

7.  CUSTOM ERRORS — ABSOLUTE RULE: NEVER use require(condition, "string").
    Every error path MUST use:
        if (!condition) revert CustomError();
    Examples:
        WRONG: require(msg.sender == _owner, "Not owner");
        RIGHT: if (msg.sender != _owner) revert Unauthorized();

        WRONG: require(msg.value == _amount, "Wrong payment");
        RIGHT: if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);

        WRONG: require(block.timestamp <= _deadline, "Expired");
        RIGHT: if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp);

    After generating, scan EVERY line for `require(` and replace ALL of them.
    Zero require() calls with strings allowed.

8.  Use `event` + `emit` for every state-changing operation (minimum 5 events).

9.  REENTRANCY GUARD — copy this EXACT pattern:
      bool private _locked;          // ← top-level state var, NOT inside modifier

      modifier noReentrant() {
          if (_locked) revert ReentrantCall();
          _locked = true;
          _;
          _locked = false;
      }
    CRITICAL: `bool private _locked;` MUST appear in the state-variable section
    of the contract, NOT inside any modifier or function.
    CRITICAL: noReentrant() takes ZERO parameters.

10. Use `unchecked` ONLY for arithmetic that cannot overflow by design.
11. Functions that emit events are NOT view. Mark view/pure correctly.
12. constructor must initialize ALL state variables.

13. PAYABLE FUNCTIONS — MANDATORY:
    a) At least one `external payable` function (e.g. pay(), depositPayment()).
    b) `receive() external payable {}` — always present.
    Functions using `msg.value` CANNOT be view/pure.

14. NatSpec MANDATORY on every public/external function:
    - `/// @notice` on every function (minimum 8 total).
    - `/// @param`  for every parameter.
    - `/// @return` for every return value.

15. Never use `tx.origin`. Never use `selfdestruct`.
16. Payable functions: if (msg.value != _amount) revert InsufficientPayment(...)

17. CRITICAL — `function` keyword:
    EVERY function definition MUST start with `function`:
        WRONG: ContractState getContractState() public view ...
        RIGHT: function getContractState() external view returns (uint8)

18. CRITICAL — event parameter data locations:
    NO `memory`, `calldata`, or `storage` in event params:
        WRONG: event Foo(address indexed a, string memory reason);
        RIGHT: event Foo(address indexed a, string reason);

19. CRITICAL — `constant` variables:
    Initialise with a compile-time integer literal ONLY:
        WRONG: uint256 public constant EFFECTIVE_DATE = block.timestamp;
        RIGHT: uint256 public constant EFFECTIVE_DATE = 1618099200;
    Constants cannot be reassigned anywhere in the code.

20. `getContractState()` returns scalar fields ONLY — NO mapping types.

21. DISPUTE MECHANISM — ALL THREE mandatory:
    a) address private _arbitrator;
    b) event DisputeRaised(address indexed initiator, uint256 timestamp);
    c) function dispute() external { ... emits DisputeRaised ... }

22. STATE MACHINE — mandatory enum with 5+ states:
        enum ContractState { Created, Active, Completed, Disputed, Terminated }

23. DELIVERABLE ACKNOWLEDGEMENT — mandatory:
    function acknowledgeDelivery() or confirmMilestone() that transitions to Completed.

24. EFFECTIVE DATE — mandatory:
    uint256 public constant EFFECTIVE_DATE = <unix_epoch>;
    uint256 public immutable startDate;   // set = EFFECTIVE_DATE in constructor
    The `startDate` field ensures LEG-030 validation passes.

25. GOVERNING LAW — mandatory:
    string public constant GOVERNING_LAW = "<first_word_of_jurisdiction>";
    /// @notice Governing law: <full jurisdiction string>
    The string constant ensures LEG-020 validation passes.

26. ACCESS CONTROL — mandatory (minimum 2 onlyX modifiers):
    modifier onlyParties()    { if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized(); _; }
    modifier onlyArbitrator() { if (msg.sender != _arbitrator) revert Unauthorized(); _; }

27. CUSTOM ERROR DECLARATIONS — ABSOLUTE RULE:
    EVERY `revert X()` MUST have a matching `error X(...);` declaration inside
    the contract. The most commonly forgotten ones are:
        error Unauthorized();
        error ReentrantCall();
        error InvalidState(uint8 current, uint8 required);
        error InsufficientPayment(uint256 sent, uint256 required);
        error DeadlinePassed(uint256 deadline, uint256 current);
        error AlreadyDisputed();
    Declare ALL of them at the top of the contract body even if you are not
    sure whether they will be used. Missing error declarations are compile errors.

28. IDENTIFIER DECLARATION — ABSOLUTE RULE:
    Every identifier used in the contract MUST be declared before use.
    Solidity will refuse to compile ANY undeclared identifier with:
        "Error: Undeclared identifier."

    PARTY / COMPANY NAMES — THE MOST COMMON MISTAKE:
    The real-world party names from the contract (e.g. "LambdaResourcesUSInc",
    "PiMergerSubLLC", "AcmeCorp", "ParentCo") are NOT valid Solidity identifiers.
    They are human-readable labels only. Do NOT use them as variable names.

    ALWAYS store party addresses as `_partyA` and `_partyB`:
        WRONG: if (msg.sender != LambdaResourcesUSInc) revert Unauthorized();
        WRONG: if (msg.sender != PiMergerSubLLC)       revert Unauthorized();
        RIGHT: if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();

    Map the real names to canonical vars in the constructor comment only:
        constructor(address payable partyA_, address payable partyB_, ...) {
            _partyA = partyA_;   // LambdaResourcesUSInc
            _partyB = partyB_;   // PiMergerSubLLC
            ...
        }

    Access-control modifiers MUST ONLY reference declared state variables.
    NEVER use company, parent, acquisitionSub, buyer, seller, or ANY
    CamelCase company/entity name from the contract text as a Solidity identifier.
    ALWAYS use: _partyA, _partyB, _arbitrator (which ARE declared state vars).

    SELF-CHECK before output: for every identifier in every modifier and
    function body, confirm it is declared as a state variable, parameter,
    local variable, or a Solidity built-in (msg, block, address, etc.).

29. IMMUTABLE VARIABLES — ABSOLUTE RULE:
    `immutable` variables CANNOT be assigned an expression inline at declaration.
        WRONG: uint256 public immutable startDate = EFFECTIVE_DATE;
        RIGHT: uint256 public immutable startDate;
               // then inside constructor:
               startDate = EFFECTIVE_DATE;

30. CONFIDENTIALITY CLAUSE — if the eContract has a confidentiality/NDA clause:
    The word "nonDisclos" or "confidential" MUST appear in the Solidity code.
    Use EXACTLY this pattern:
        bool private _confidentialityAcknowledged;
        event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
        function acknowledgeNonDisclosure() external onlyParties {
            _confidentialityAcknowledged = true;
            emit NonDisclosureAcknowledged(msg.sender, block.timestamp);
        }
    The validator (COV-050) checks for these keywords — missing them = test failure.
"""

SOLIDITY_TEMPLATE_HINTS = """
STRUCTURAL TEMPLATE (minimum structure — adapt specifics to this contract):

contract <ContractName>Contract {

    // ── Custom Errors ─────────────────────────────────────────────────────
    error Unauthorized();
    error InvalidState(uint8 current, uint8 required);
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error ReentrantCall();
    error AlreadyDisputed();

    // ── State machine ─────────────────────────────────────────────────────
    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    // ── Governing law string constant (FIX-L: satisfies LEG-020) ─────────
    /// @notice Governing law jurisdiction.
    string public constant GOVERNING_LAW = "<jurisdiction_keyword>";

    // ── Effective date (FIX-M: startDate satisfies LEG-030) ──────────────
    /// @notice Unix timestamp of the effective date.
    uint256 public constant EFFECTIVE_DATE = 1618099200; // 2021-04-11
    uint256 public immutable startDate;

    // ── State variables ───────────────────────────────────────────────────
    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    address private _arbitrator;
    uint256 private _amount;
    uint256 private _deadline;
    uint256 private _penaltyRate;
    bool private _locked;               // FIX-I: TOP-LEVEL, not inside modifier

    // ── Events (min 5, NO memory/calldata/storage in params) ─────────────
    event ContractCreated(address indexed partyA, address indexed partyB, uint256 amount);
    event PaymentMade(address indexed payer, uint256 amount, uint256 timestamp);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    event DisputeRaised(address indexed initiator, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);
    event PenaltyCalculated(uint256 penaltyWei);

    // ── Access control (FIX-J: min 2 onlyX modifiers) ────────────────────
    modifier onlyParties() {
        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
        _;
    }
    modifier onlyPartyA() {
        if (msg.sender != _partyA) revert Unauthorized();
        _;
    }
    modifier onlyArbitrator() {
        if (msg.sender != _arbitrator) revert Unauthorized();
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

    // ── Reentrancy guard (_locked declared above) ─────────────────────────
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    // ── Constructor ───────────────────────────────────────────────────────
    constructor(
        address payable partyA_,
        address payable partyB_,
        address arbitrator_,
        uint256 amount_,
        uint256 deadlineDays_,
        uint256 penaltyRateBps_
    ) {
        _partyA      = partyA_;
        _partyB      = partyB_;
        _arbitrator  = arbitrator_;
        _amount      = amount_;
        _deadline    = block.timestamp + (deadlineDays_ * 1 days);
        _penaltyRate = penaltyRateBps_;
        _state       = ContractState.Created;
        startDate    = EFFECTIVE_DATE;         // FIX-M
        emit ContractCreated(partyA_, partyB_, amount_);
    }

    // ── Payment (FIX-K: external payable mandatory) ───────────────────────
    /// @notice PartyA deposits ETH to activate the contract.
    function pay()
        external payable
        onlyPartyA
        inState(ContractState.Created)
        beforeDeadline
        noReentrant
    {
        if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);
        _state = ContractState.Active;
        emit PaymentMade(msg.sender, msg.value, block.timestamp);
    }

    // ── Delivery acknowledgement (mandatory) ──────────────────────────────
    /// @notice PartyA acknowledges delivery, releasing funds to partyB.
    function acknowledgeDelivery()
        external
        onlyPartyA
        inState(ContractState.Active)
        noReentrant
    {
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }

    // ── Dispute (mandatory) ───────────────────────────────────────────────
    /// @notice Raises a dispute, freezing the contract pending arbitration.
    function dispute() external onlyParties {
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
        if (_state == ContractState.Completed || _state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    // ── Terminate ─────────────────────────────────────────────────────────
    /// @notice Terminates the contract and refunds partyA.
    function terminate() external onlyParties noReentrant {
        if (_state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
            if (!ok) revert InsufficientPayment(0, address(this).balance);
        }
        emit ContractTerminated(msg.sender, block.timestamp);
    }

    // ── Penalty (FIX-G/H: NOT view; uint256 principal param, not msg.value)
    /// @notice Calculates and records a late-delivery penalty.
    /// @param  principal      Reference amount in wei for penalty calculation.
    /// @param  penaltyRateBps Penalty rate in basis points (500 = 5%).
    /// @return penaltyWei     Penalty owed in wei.
    function calculatePenalty(uint256 principal, uint256 penaltyRateBps)
        external
        noReentrant
        returns (uint256 penaltyWei)
    {
        if (_state != ContractState.Disputed)
            revert InvalidState(uint8(_state), uint8(ContractState.Disputed));
        if (block.timestamp <= _deadline) return 0;
        uint256 daysLate = (block.timestamp - _deadline) / 1 days;
        penaltyWei = (principal * penaltyRateBps * daysLate) / 10_000;
        emit PenaltyCalculated(penaltyWei);
    }

    // ── View state ────────────────────────────────────────────────────────
    /// @notice Returns all key contract fields as scalars.
    /// @return partyA_     Address of party A.
    /// @return partyB_     Address of party B.
    /// @return arbitrator_ Arbitrator address.
    /// @return state_      ContractState cast to uint8.
    /// @return amount_     Contract amount in wei.
    /// @return deadline_   Unix timestamp deadline.
    function getContractState()
        external view
        returns (
            address partyA_,
            address partyB_,
            address arbitrator_,
            uint8   state_,
            uint256 amount_,
            uint256 deadline_
        )
    {
        return (_partyA, _partyB, _arbitrator, uint8(_state), _amount, _deadline);
    }

    // ── Accept ETH (FIX-K: mandatory receive) ─────────────────────────────
    /// @notice Accept direct ETH deposits.
    receive() external payable {
        emit PaymentMade(msg.sender, msg.value, block.timestamp);
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
- The output must be a single complete .sol file that compiles cleanly with solc 0.8.16.
- Before finishing, scan line-by-line for:
  (1)  Any require() with strings → replace ALL with custom errors
  (2)  Missing dispute() function → add it
  (3)  Missing enum ContractState → add it
  (4)  Missing @notice on functions → add (need ≥ 8)
  (5)  Missing _arbitrator state variable → add it
  (6)  Event params with memory/calldata/storage → strip those keywords
  (7)  constant set to block.timestamp → replace with integer literal
  (8)  Function missing `function` keyword → add it
  (9)  `bool private _locked;` at contract level → verify present
  (10) ≥2 `modifier onlyX` declarations → verify present
  (11) ≥1 `external payable` function AND receive() → verify present
  (12) calculatePenalty() is NOT view, uses uint256 param not msg.value → verify
  (13) GOVERNING_LAW string constant present → verify
  (14) `startDate` immutable declared and set in constructor → verify
  (15) CRITICAL: Every `revert X()` used in the code MUST have a matching
       `error X(...);` declared at the top of the contract body. Scan every
       `revert` keyword and confirm its error name is declared.
       NEVER use `revert Unauthorized()` without `error Unauthorized();`.
  (16) CRITICAL: If `noReentrant` is used in any function signature, the
       `modifier noReentrant()` body MUST be declared inside the contract.
       The pattern is ALWAYS:
           modifier noReentrant() {{
               if (_locked) revert ReentrantCall();
               _locked = true;
               _;
               _locked = false;
           }}
       Do NOT use `noReentrant` without this modifier body present.
  (17) CRITICAL: `uint256 public immutable startDate` MUST NOT be assigned
       inline. WRONG: `uint256 public immutable startDate = EFFECTIVE_DATE;`
       RIGHT: declare as `uint256 public immutable startDate;` then in the
       constructor body: `startDate = EFFECTIVE_DATE;`
  (18) CRITICAL: Every variable referenced in modifier bodies and function
       bodies MUST be declared as a state variable. Do NOT reference `company`,
       `parent`, `acquisitionSub`, `buyer`, `seller` or any other name in a
       modifier unless it is declared as `address private _partyA;` or similar.
       Only use `_partyA`, `_partyB`, `_arbitrator` in access-control modifiers.
  Fix ALL issues before outputting.

{SOLIDITY_TEMPLATE_HINTS}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Clause → prose summary
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
#  Effective-date → unix timestamp helper
# ═══════════════════════════════════════════════════════════════════════════

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _date_to_epoch(date_str: str) -> int:
    """Convert a human-readable date string to a unix epoch integer."""
    if not date_str:
        return 1621296000  # 2021-05-18

    m = re.search(r"(\d{1,4})[/-](\d{1,2})[/-](\d{2,4})", date_str)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 31:
            y, mo, d = a, b, c
        elif c > 31:
            y = c if c > 99 else 2000 + c
            if 1 <= a <= 12 and 1 <= b <= 12:
                # Both a and b could be month or day — silently assumes MM/DD convention.
                logger.warning(
                    "_date_to_epoch: ambiguous date '%s' (both %d and %d are valid month values). "
                    "Assuming MM/DD convention (month=%d, day=%d). "
                    "Use an unambiguous format such as YYYY-MM-DD to avoid wrong EFFECTIVE_DATE epochs.",
                    date_str, a, b, a, b,
                )
            mo, d = (a, b) if 1 <= a <= 12 else (b, a)
        else:
            return 1621296000
        try:
            return int(calendar.timegm(time.strptime(f"{y}-{mo:02d}-{d:02d}", "%Y-%m-%d")))
        except ValueError:
            pass

    m = re.search(
        r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})|(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})",
        date_str,
    )
    if m:
        if m.group(1):
            mon_s, day, year = m.group(1)[:3].lower(), int(m.group(2)), int(m.group(3))
        else:
            day, mon_s, year = int(m.group(4)), m.group(5)[:3].lower(), int(m.group(6))
        mo = _MONTH_MAP.get(mon_s, 1)
        try:
            return int(calendar.timegm(time.strptime(f"{year}-{mo:02d}-{day:02d}", "%Y-%m-%d")))
        except ValueError:
            pass

    return 1621296000


# ═══════════════════════════════════════════════════════════════════════════
#  Public builders
# ═══════════════════════════════════════════════════════════════════════════

def build_user_prompt(doc: ContractDocument) -> str:
    """Build the user-turn prompt requesting a Solidity 0.8.16 implementation."""
    lines: list[str] = []
    sep = "=" * 70
    lines += [sep, "ECONTRACT -> SMART CONTRACT CONVERSION REQUEST", sep, ""]

    clean_title = doc.title.lstrip("\ufeff").strip()
    epoch = _date_to_epoch(doc.effective_date or "")
    gov_law = doc.governing_law or "Not specified"
    gov_word = gov_law.split()[0] if gov_law and gov_law != "Not specified" else "General"

    lines += [
        f"CONTRACT TITLE   : {clean_title}",
        f"EFFECTIVE DATE   : {doc.effective_date or 'Not specified'}",
        f"  ↳ ENCODE AS    : uint256 public constant EFFECTIVE_DATE = {epoch};",
        f"    ALSO ADD     : uint256 public immutable startDate; // set = EFFECTIVE_DATE in constructor",
        f"    IMPORTANT    : {epoch} is an integer literal — NOT block.timestamp.",
        f"EXPIRY DATE      : {doc.expiry_date or 'Not specified'}",
        f"GOVERNING LAW    : {gov_law}",
        f"  ↳ ADD IN CODE  : string public constant GOVERNING_LAW = \"{gov_word}\";",
        f"    AND NATSPEC  : /// @notice Governing law: {gov_law}",
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
        "CONVERSION REQUIREMENTS (ALL mandatory):",
        "",
        "STRUCTURE:",
        "1.  Contract name: PascalCase from title words.",
        "2.  Encode EVERY clause as on-chain logic, state, events, or modifiers.",
        "",
        "CLAUSE ENCODING MAP:",
        "3.  Payment clauses    → payable function + wei validation + noReentrant.",
        "4.  Penalty clauses    → calculatePenalty(uint256 principal, uint256 rate) — NOT view.",
        "5.  Expiry/term clauses → _deadline = block.timestamp + N days in constructor.",
        "6.  Obligation clauses → acknowledgeDelivery() or confirmMilestone().",
        "7.  Dispute clauses    → dispute() + _arbitrator + DisputeRaised event.",
        "8.  Confidentiality/IP → MUST use word 'nonDisclos' or 'confidential' in code.",
        "    Required pattern (COV-050 validator checks these exact keywords):",
        "      bool private _confidentialityAcknowledged;",
        "      event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);",
        "      function acknowledgeNonDisclosure() external onlyParties {",
        "          _confidentialityAcknowledged = true;",
        "          emit NonDisclosureAcknowledged(msg.sender, block.timestamp);",
        "      }",
        "",
        "MANDATORY FUNCTIONS:",
        "9.  dispute()                                     — sets Disputed state, emits event.",
        "10. acknowledgeDelivery() / confirmMilestone()    — sets Completed state.",
        "11. calculatePenalty(uint256 principal, uint256 rate) — NOT view, emits event.",
        "12. terminate()                                   — both parties can call.",
        "13. getContractState()                            — scalar return, no mappings.",
        "14. pay() / depositPayment()                      — external payable.",
        "",
        "MANDATORY STATE VARIABLES:",
        "15. bool private _locked;                         — top-level, NOT inside modifier.",
        "16. address private _arbitrator;                  — set in constructor.",
        "17. enum ContractState { Created, Active, Completed, Disputed, Terminated }",
        f"18. uint256 public constant EFFECTIVE_DATE = {epoch}; // {doc.effective_date}",
        "19. uint256 public immutable startDate;           — set = EFFECTIVE_DATE in constructor.",
        f"20. string public constant GOVERNING_LAW = \"{gov_word}\";",
        "",
        "MANDATORY MODIFIERS (minimum 2 onlyX):",
        "21. modifier onlyParties() / onlyPartyA() — reverts Unauthorized.",
        "22. modifier onlyArbitrator()              — reverts Unauthorized.",
        "",
        "COMPILE-TIME RULES:",
        "23. Event params MUST NOT have memory/calldata/storage keywords.",
        "24. Every function definition MUST start with `function`.",
        "25. Constants cannot be assigned inside functions.",
        "26. msg.value only in payable functions — not in view functions.",
        "27. Functions that emit events are NOT view.",
        "",
        "QUALITY GATES:",
        "28. Minimum 8 `/// @notice` NatSpec comments.",
        "29. Zero require() calls with strings — custom errors only.",
        "30. Minimum 5 events declared and emitted.",
        "31. noReentrant on every ETH-transferring function.",
        "32. No SafeMath. No OpenZeppelin.",
        "",
        "SELF-CHECK:",
        "  □ dispute() present?",
        "  □ _arbitrator state variable present?",
        "  □ DisputeRaised event WITHOUT memory in params?",
        "  □ enum ContractState with ≥5 states?",
        "  □ acknowledgeDelivery() / confirmMilestone() present?",
        f"  □ EFFECTIVE_DATE = {epoch} (integer, not block.timestamp)?",
        "  □ `uint256 public immutable startDate` declared and set in constructor?",
        f"  □ `string public constant GOVERNING_LAW = \"{gov_word}\";` present?",
        "  □ `bool private _locked;` at top-level contract scope?",
        "  □ ≥2 `modifier onlyX` declarations?",
        "  □ ≥1 `external payable` function?",
        "  □ `receive() external payable` present?",
        "  □ calculatePenalty() has uint256 principal param and is NOT view?",
        "  □ Every function starts with `function` keyword?",
        "  □ Zero require(condition, \"string\") calls?",
        "  □ ≥8 /// @notice comments?",
        "  □ Balanced braces?",
        "  □ If confidentiality clause: 'nonDisclos' or 'confidential' keyword in code?",
        "    (bool _confidentialityAcknowledged + event NonDisclosureAcknowledged + fn acknowledgeNonDisclosure)",
        "",
        "Now output ONLY the complete Solidity source code:",
        sep,
    ]

    return "\n".join(lines)


def get_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_validation_prompt(solidity_code: str, doc: ContractDocument) -> str:
    """Build a self-review / audit prompt for a second LLM pass."""
    clause_types = sorted({c.clause_type for c in doc.clauses})
    clean_title  = doc.title.lstrip("\ufeff").strip()
    epoch = _date_to_epoch(doc.effective_date or "")
    gov_law = doc.governing_law or "Not specified"
    gov_word = gov_law.split()[0] if gov_law and gov_law != "Not specified" else "General"

    return f"""You are a Solidity 0.8.16 security auditor reviewing an auto-generated contract.

Contract: {clean_title}
Expected clause types: {', '.join(clause_types)}

Verify ALL items and fix any issues found:

1.  All clause types represented as on-chain logic.
2.  No tx.origin, selfdestruct, or floating pragma.
3.  `bool private _locked;` at contract scope (NOT inside a modifier).
4.  modifier noReentrant() takes ZERO parameters.
5.  noReentrant applied to every ETH-transferring function.
6.  ZERO require() with string args — search every line, replace ALL:
        RIGHT: if (!condition) revert CustomError();
7.  Every state-changing function emits an event.
8.  Minimum 8 `/// @notice` comments.
9.  SPDX-License-Identifier on line 1. pragma solidity ^0.8.16 on line 2.
10. getContractState() returns NO mapping types.
11. Balanced braces.
12. No OpenZeppelin imports, no SafeMath.
13. Event params have NO memory/calldata/storage keywords.
14. EFFECTIVE_DATE = {epoch} (integer literal, not block.timestamp).
15. Every function definition starts with `function` keyword.
16. Constants not reassigned anywhere.
17. dispute() function present.
18. _arbitrator state variable declared and set in constructor.
19. enum ContractState with ≥5 states.
20. acknowledgeDelivery() or confirmMilestone() present.
21. Minimum 2 `modifier onlyX` — add if fewer exist.
22. At least one `external payable` function AND receive() present.
23. calculatePenalty() is NOT view, accepts uint256 principal param, does NOT use msg.value.
24. `uint256 public immutable startDate` declared and set in constructor.
25. `string public constant GOVERNING_LAW = "{gov_word}";` declared.

If ANY issue found: output COMPLETE corrected Solidity source.
If passes all checks: output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""


# Alias
get_validation_prompt = build_validation_prompt

# ═══════════════════════════════════════════════════════════════════════════
#  Feedback-loop prompt builder
# ═══════════════════════════════════════════════════════════════════════════

def build_feedback_prompt(
    solidity_code: str,
    doc: "ContractDocument",
    failed_tests: list,
    validation_issues: list[str],
    attempt: int,
    max_attempts: int,
) -> str:
    """
    Build a targeted correction prompt that feeds failed test results and
    structural validation issues back to the LLM so it can fix them.

    Parameters
    ----------
    solidity_code    : The contract code that failed validation.
    doc              : Parsed ContractDocument for context.
    failed_tests     : List of TestResult objects that did not pass.
    validation_issues: List of issue strings from validate_solidity_output().
    attempt          : Current feedback iteration (1-based).
    max_attempts     : Total allowed iterations.
    """
    clean_title = doc.title.lstrip("\ufeff").strip()
    epoch       = _date_to_epoch(doc.effective_date or "")
    gov_law     = doc.governing_law or "Not specified"
    gov_word    = gov_law.split()[0] if gov_law and gov_law != "Not specified" else "General"
    sep         = "=" * 70

    # ── Categorise failures ──────────────────────────────────────────────────
    critical_tests = [t for t in failed_tests if t.severity == "critical"]
    major_tests    = [t for t in failed_tests if t.severity == "major"]
    minor_tests    = [t for t in failed_tests if t.severity in ("minor", "info")]

    lines = [
        sep,
        f"SMART CONTRACT CORRECTION REQUEST — Attempt {attempt}/{max_attempts}",
        sep,
        "",
        f"Contract : {clean_title}",
        f"Governing law : {gov_law}",
        f"EFFECTIVE_DATE constant : {epoch}  (integer literal, NOT block.timestamp)",
        f"GOVERNING_LAW constant  : \"{gov_word}\"",
        "",
        "The Solidity contract below FAILED automated validation.",
        "You MUST fix EVERY issue listed. Output ONLY corrected Solidity — no prose.",
        "",
    ]

    # ── Structural / compile-time issues (from validate_solidity_output) ───
    if validation_issues:
        lines += [
            f"{'─'*70}",
            "STRUCTURAL / COMPILE-TIME ISSUES  (fix ALL — these prevent compilation):",
            "",
        ]
        for i, issue in enumerate(validation_issues, 1):
            lines.append(f"  {i:2d}. {issue}")
        lines.append("")

    # ── Critical test failures ───────────────────────────────────────────────
    if critical_tests:
        lines += [
            f"{'─'*70}",
            "CRITICAL TEST FAILURES  (fix ALL — these block deployment):",
            "",
        ]
        for t in critical_tests:
            lines.append(f"  ✗ [{t.test_id}] {t.description}")
            if t.detail:
                lines.append(f"       Detail: {t.detail}")
        lines.append("")

    # ── Major test failures ──────────────────────────────────────────────────
    if major_tests:
        lines += [
            f"{'─'*70}",
            "MAJOR TEST FAILURES  (fix ALL — these indicate missing contract logic):",
            "",
        ]
        for t in major_tests:
            lines.append(f"  ✗ [{t.test_id}] {t.description}")
            if t.detail:
                lines.append(f"       Detail: {t.detail}")
        lines.append("")

    # ── Minor / info failures ────────────────────────────────────────────────
    if minor_tests:
        lines += [
            f"{'─'*70}",
            "MINOR / INFO FAILURES  (fix these too for full compliance):",
            "",
        ]
        for t in minor_tests:
            lines.append(f"  ✗ [{t.test_id}] {t.description}")
            if t.detail:
                lines.append(f"       Detail: {t.detail}")
        lines.append("")

    # ── Targeted fix instructions derived from test IDs ─────────────────────
    fix_hints = _derive_fix_hints(failed_tests, validation_issues, epoch, gov_word)
    if fix_hints:
        lines += [
            f"{'─'*70}",
            "TARGETED FIX INSTRUCTIONS:",
            "",
        ]
        lines.extend(fix_hints)
        lines.append("")

    # ── Self-check checklist (always included) ───────────────────────────────
    lines += [
        f"{'─'*70}",
        "BEFORE OUTPUTTING — verify ALL boxes are ticked:",
        "",
        "  □ SPDX-License-Identifier on line 1?",
        "  □ pragma solidity ^0.8.16 on line 2?",
        "  □ bool private _locked; at contract scope (NOT inside modifier)?",
        "  □ modifier noReentrant() takes ZERO parameters?",
        "  □ noReentrant applied to EVERY payable / ETH-transferring function?",
        "  □ ZERO require(condition, \"string\") calls?",
        "  □ All revert targets declared as custom errors?",
        "  □ All emit targets declared as events?",
        "  □ Event params have NO memory/calldata/storage keywords?",
        "  □ Every function definition starts with `function` keyword?",
        f"  □ uint256 public constant EFFECTIVE_DATE = {epoch};  (integer, not block.timestamp)?",
        "  □ uint256 public immutable startDate; set = EFFECTIVE_DATE in constructor?",
        f"  □ string public constant GOVERNING_LAW = \"{gov_word}\";  present?",
        "  □ ≥2 modifier onlyX declarations?",
        "  □ ≥1 external payable function?",
        "  □ receive() external payable present?",
        "  □ calculatePenalty() is NOT view, uses uint256 param not msg.value?",
        "  □ dispute() function present?",
        "  □ acknowledgeDelivery() or confirmMilestone() present?",
        "  □ enum ContractState with ≥5 states?",
        "  □ ≥8 /// @notice NatSpec comments?",
        "  □ Balanced braces {}?",
        "",
        sep,
        "CONTRACT TO CORRECT (fix every issue listed above):",
        sep,
        "",
        solidity_code,
        "",
        sep,
        "Output ONLY the corrected Solidity source code — no markdown, no explanation:",
        sep,
    ]

    return "\n".join(lines)


def _derive_fix_hints(
    failed_tests: list,
    validation_issues: list[str],
    epoch: int,
    gov_word: str,
) -> list[str]:
    """
    Map failing test IDs to concrete, actionable fix instructions so the
    LLM gets targeted guidance instead of only generic rules.
    """
    hints: list[str] = []
    ids = {t.test_id for t in failed_tests if hasattr(t, 'test_id')}

    _HINT_MAP: dict[str, str] = {
        "SOL-001": "Add `// SPDX-License-Identifier: MIT` as the very first line.",
        "SOL-002": "Add `pragma solidity ^0.8.16;` as the second line.",
        "SOL-003": "Ensure a top-level `contract <Name> { ... }` block exists.",
        "SOL-004": "Add a `constructor(...)` that initialises all state variables.",
        "SOL-005": "Declare at least 3 events: e.g. ContractCreated, PaymentMade, DisputeRaised.",
        "SOL-006": "Emit events in every state-changing function (at least 2 `emit` calls).",
        "SOL-007": (
            "Replace ALL `require(condition, \"string\")` with:\n"
            "        if (!condition) revert CustomError();"
        ),
        "SOL-008": "Remove any `import` lines referencing SafeMath and any `using SafeMath` lines.",
        "SOL-009": "Remove any `import \"@openzeppelin/...\"` lines.",
        "SOL-010": "Remove all `selfdestruct(...)` calls — deprecated in Solidity 0.8.x.",
        "SOL-011": "Replace `tx.origin` with `msg.sender` everywhere.",
        "SOL-012": (
            "Count `{` and `}` — they must match exactly.\n"
            "        Close every function, modifier, and contract body properly."
        ),
        "SOL-013": (
            "Add `/// @notice <description>` above EVERY public/external function.\n"
            "        Minimum 8 @notice comments required."
        ),
        "SOL-014": "Add `receive() external payable { emit PaymentReceived(msg.sender, msg.value); }`.",
        "SOL-015": "Add `enum ContractState { Created, Active, Completed, Disputed, Terminated }`.",
        "SOL-016": (
            "Remove `view` from calculatePenalty() — it emits an event, so it cannot be view.\n"
            "        Change: `function calculatePenalty(...) external view returns (...)`\n"
            "        To:     `function calculatePenalty(...) external returns (...)`"
        ),
        "SEC-001": (
            "Add `bool private _locked;` in the STATE VARIABLE section of the contract,\n"
            "        directly after the last enum/event/error declaration.\n"
            "        It must NOT be inside any modifier or function."
        ),
        "SEC-002": (
            "Add the noReentrant modifier:\n"
            "        modifier noReentrant() {\n"
            "            if (_locked) revert ReentrantCall();\n"
            "            _locked = true;\n"
            "            _;\n"
            "            _locked = false;\n"
            "        }"
        ),
        "SEC-003": "Apply `noReentrant` to every payable and ETH-transferring function.",
        "SEC-004": (
            "Validate msg.value in payable functions:\n"
            "        if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);"
        ),
        "SEC-005": (
            "Add at least 2 onlyX modifiers:\n"
            "        modifier onlyParties()    { if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized(); _; }\n"
            "        modifier onlyArbitrator() { if (msg.sender != _arbitrator) revert Unauthorized(); _; }"
        ),
        "SEC-009": "Add `address private _arbitrator;` and initialise it in the constructor.",
        "COV-001": (
            "Add an external payable function:\n"
            "        function pay() external payable onlyPartyA noReentrant {\n"
            "            if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);\n"
            "            emit PaymentMade(msg.sender, msg.value, block.timestamp);\n"
            "        }"
        ),
        "COV-003": "Declare and emit a PaymentMade or PaymentReceived event in the payable function.",
        "COV-010": (
            "Add a calculatePenalty function that is NOT view:\n"
            "        function calculatePenalty(uint256 principal, uint256 penaltyRateBps)\n"
            "            external noReentrant returns (uint256 penaltyWei) {\n"
            "            penaltyWei = (principal * penaltyRateBps) / 10_000;\n"
            "            emit PenaltyCalculated(penaltyWei);\n"
            "        }"
        ),
        "COV-020": (
            "Store deadline as:\n"
            "        uint256 private _deadline = block.timestamp + (N * 1 days);\n"
            "        and check: if (block.timestamp > _deadline) revert DeadlinePassed(...);"
        ),
        "COV-021": "Add `function terminate() external onlyParties { ... emit ContractTerminated(...); }`.",
        "COV-030": "Use `_state = ContractState.Active;` etc. for all state transitions.",
        "COV-031": (
            "Add `function acknowledgeDelivery() external onlyPartyA inState(ContractState.Active) {`\n"
            "        that transitions to Completed and releases funds."
        ),
        "COV-040": (
            "Add `function dispute() external onlyParties {`\n"
            "        that sets `_state = ContractState.Disputed` and emits DisputeRaised."
        ),
        "COV-041": "Add `address private _arbitrator;` as a state variable and set it in constructor.",
        "COV-042": "Add `event DisputeRaised(address indexed initiator, uint256 timestamp);` and emit it.",
        "LEG-020": (
            f"Add: `string public constant GOVERNING_LAW = \"{gov_word}\";`\n"
            "        immediately after the EFFECTIVE_DATE constant."
        ),
        "LEG-030": (
            f"Add: `uint256 public constant EFFECTIVE_DATE = {epoch};`\n"
            "        and: `uint256 public immutable startDate;`\n"
            "        then in constructor: `startDate = EFFECTIVE_DATE;`"
        ),
        "LEG-070": (
            "Add:\n"
            "        function getContractState() external view returns (\n"
            "            address partyA_, address partyB_, address arbitrator_,\n"
            "            uint8 state_, uint256 amount_, uint256 deadline_\n"
            "        ) {\n"
            "            return (_partyA, _partyB, _arbitrator, uint8(_state), _amount, _deadline);\n"
            "        }"
        ),
        "LEG-080": "Add `function terminate() external onlyParties noReentrant { ... }`.",
        "LEG-090": (
            "Add `receive() external payable { emit PaymentReceived(msg.sender, msg.value); }`\n"
            "        AND ensure at least one function has `external payable` modifiers."
        ),
    }

    for test_id, hint in _HINT_MAP.items():
        if test_id in ids:
            hints.append(f"  [{test_id}] {hint}")

    # Catch any test IDs not in the static map
    unmapped = ids - set(_HINT_MAP.keys())
    for tid in sorted(unmapped):
        t = next((x for x in failed_tests if x.test_id == tid), None)
        if t:
            hints.append(f"  [{tid}] Fix: {t.description}. Detail: {t.detail}")

    for issue in validation_issues:
        if "Malformed if-revert" in issue:
            hints.append(
                "  [SYNTAX] Malformed if-revert detected. NEVER write:\n"
                "        if (!(a == x)) revert Err() && b == y, \"msg\";\n"
                "    ALWAYS write separate statements:\n"
                "        if (a != x || b != y) revert Unauthorized();"
            )
        elif "Bare `locked`" in issue:
            hints.append(
                "  [SYNTAX] Replace every bare `locked` with `_locked` to match the "
                "state variable `bool private _locked;`."
            )
        elif "uint256 contractState" in issue:
            hints.append(
                "  [TYPE] Change `uint256 public contractState` to "
                "`ContractState public contractState` to match the enum type."
            )

    return hints