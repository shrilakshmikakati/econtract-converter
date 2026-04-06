"""
prompt_builder.py — Converts a ContractDocument into a precision-crafted
prompt that instructs the LLM to generate a Solidity 0.8.16 smart contract.

FIXES vs previous version (targeting failures in results.json):

  FIX A — COV-040 / CLS-DISPUT (CRITICAL): dispute() function missing.
    Root cause: the user prompt listed "7. Dispute clauses -> dispute()
    function + arbitrator address" in a long list of soft requirements.
    The LLM de-prioritised it for merger-type contracts.
    Fix: dispute() is now a MANDATORY section with its own rule block (rule 21)
    and a full inline code example showing the exact expected pattern.

  FIX B — COV-030 / CLS-OBLIGA / SOL-015: No enum state machine.
    Root cause: state machine was implied by the template but never mandated
    with enforcement language. For complex contracts the LLM skipped it.
    Fix: rule 22 mandates an enum ContractState with ≥ 5 states; its use in
    every transition function is now an explicit requirement.

  FIX C — COV-031: No deliverable/milestone acknowledgement function.
    Root cause: "obligation clauses → state machine transitions" was listed
    but "acknowledge delivery" was never named.
    Fix: rule 23 requires an explicit acknowledgeDelivery() or
    confirmMilestone() function that transitions state.

  FIX D — SEC-009 / COV-041 / COV-042: Arbitrator address + dispute event.
    Root cause: these were sub-items of the dispute bullet that got dropped.
    Fix: they are now explicit sub-rules of rule 21 with exact identifiers.

  FIX E — SOL-007: require() strings still appearing.
    Root cause: rule 7 said "NEVER" but template hints still contained
    require() examples that leaked into generation.
    Fix: all template comments that could be read as require() examples removed;
    added a CODE TRANSFORMATION table showing wrong→right conversions.

  FIX F — SOL-013: Only 1 @notice (needs ≥ 2).
    Root cause: "NatSpec on every function" wasn't enforced with a minimum.
    Fix: rule 14 now says "EVERY public/external function and state variable
    MUST have a @notice comment — minimum 5 @notice comments in the file".

  FIX G — LEG-030: Effective date not encoded.
    Root cause: effective date was printed in the header but never mapped
    to a Solidity constant/comment requirement.
    Fix: rule 24 mandates encoding the effective date as a uint256 constant
    and a comment, and the user prompt highlights it.

  FIX H — SOL-007 (partial): 2 residual require-strings.
    Root cause: the wrong→right table wasn't present; the LLM reverted.
    Fix: added the table + a second-pass reminder to scan for all require().

  RETAINED FIXES from previous version:
    FIX 1 — noReentrant with zero parameters.
    FIX 2 — noReentrant body uses custom error, not require string.
    FIX 3 — getContractState cannot return mapping types.
    FIX 4 — build_validation_prompt checklist extended.
"""

from __future__ import annotations

from extractor import ContractDocument, ContractClause


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity rules injected into the system prompt
# ═══════════════════════════════════════════════════════════════════════════

SOLIDITY_RULES = """
MANDATORY SOLIDITY 0.8.16 RULES — follow EVERY rule, no exceptions:

1.  First line MUST be: // SPDX-License-Identifier: MIT
2.  Second line MUST be: pragma solidity ^0.8.16;
3.  Use `address payable` for addresses that receive ETH.
4.  All ETH amounts in the contract are in WEI (1 ETH = 1e18 wei).
5.  Use `block.timestamp` for time; deadlines are unix epoch seconds.
6.  State variables: use `private` + getter unless external access required.

7.  CUSTOM ERRORS — ABSOLUTE RULE: NEVER use require(condition, "string").
    Every single error path MUST use the pattern:
        if (!condition) revert CustomError();
    TRANSFORMATION TABLE (commit this to memory):
        WRONG: require(msg.sender == _owner, "Not owner");
        RIGHT: if (msg.sender != _owner) revert Unauthorized();

        WRONG: require(msg.value == _amount, "Wrong payment");
        RIGHT: if (msg.value != _amount) revert InsufficientPayment(msg.value, _amount);

        WRONG: require(block.timestamp <= _deadline, "Expired");
        RIGHT: if (block.timestamp > _deadline) revert DeadlinePassed(_deadline, block.timestamp);
    After generating the contract, scan every single line for `require(` and
    replace it with the custom-error pattern. Zero require() calls with strings
    are acceptable.

8.  Use `event` + `emit` for every state-changing operation (minimum 5 events).

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
12. constructor must initialize ALL state variables including arbitrator, deadline,
    effectiveDate, and the initial ContractState enum value.

13. Implement `receive() external payable {}` if the contract holds ETH.

14. NatSpec MANDATORY on EVERY public/external function and every state variable:
    - Use `/// @notice` on every function (minimum 5 @notice comments total).
    - Use `/// @param` for every parameter.
    - Use `/// @return` for every return value.
    Example:
        /// @notice Initiates a dispute and notifies the arbitrator.
        /// @param reason Short string key identifying the dispute reason.
        function dispute(string calldata reason) external { ... }

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

21. DISPUTE MECHANISM — MANDATORY (no exceptions, even for merger contracts):
    You MUST implement ALL THREE of the following:
    a) State variable:
           address private _arbitrator;
       Initialised in constructor. Publicly readable via getContractState().
    b) Dispute event:
           event DisputeRaised(address indexed initiator, uint256 timestamp);
       Emitted inside dispute().
    c) Dispute function:
           /// @notice Raises a dispute and freezes the contract pending arbitration.
           function dispute() external {
               if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
               if (_state == ContractState.Disputed) revert InvalidState(uint8(_state), uint8(ContractState.Disputed));
               _state = ContractState.Disputed;
               emit DisputeRaised(msg.sender, block.timestamp);
           }
    Omitting any of these three items is a CRITICAL failure.

22. STATE MACHINE — MANDATORY enum with minimum 5 states:
        enum ContractState { Created, Active, Completed, Disputed, Terminated }
    - The constructor sets _state = ContractState.Created.
    - Every state-changing function MUST:
        * Check the current state with a modifier or inline check.
        * Assign _state = ContractState.<NewState>.
        * Emit an event documenting the transition.
    - Use `inState(ContractState s)` modifier to guard transitions:
        modifier inState(ContractState s) {
            if (_state != s) revert InvalidState(uint8(_state), uint8(s));
            _;
        }

23. DELIVERABLE / MILESTONE ACKNOWLEDGEMENT — MANDATORY:
    Implement at least ONE of these functions (name it appropriately):
        acknowledgeDelivery()  — called by the receiving party to confirm receipt.
        confirmMilestone()     — called by the client to confirm a milestone.
    This function MUST:
        * Require the caller is the appropriate party (revert Unauthorized()).
        * Require the contract is in ContractState.Active.
        * Transition state to ContractState.Completed.
        * Emit a completion/acknowledgement event.

24. EFFECTIVE DATE — MANDATORY encoding:
    Encode the effective date from the contract as a uint256 constant:
        uint256 public constant EFFECTIVE_DATE = <unix_timestamp>;  // e.g. 2021-04-11 = 1618099200
        // Governing law: <jurisdiction string>
    If the exact unix timestamp is uncertain, use a best-estimate value and add
    a comment with the human-readable date.
"""

# ── Template (FIX E: all require() examples removed) ────────────────────────
SOLIDITY_TEMPLATE_HINTS = """
STRUCTURAL TEMPLATE (adapt to contract specifics — this is the minimum structure):

contract <ContractName>Contract {

    // ── Custom Errors ────────────────────────────────────────────────────
    error Unauthorized();
    error InvalidState(uint8 current, uint8 required);
    error DeadlinePassed(uint256 deadline, uint256 current);
    error InsufficientPayment(uint256 sent, uint256 required);
    error ReentrantCall();
    error AlreadyDisputed();
    error ContractExpired();

    // ── Events (minimum 5) ───────────────────────────────────────────────
    event ContractCreated(address indexed partyA, address indexed partyB, uint256 amount);
    event PaymentMade(address indexed payer, uint256 amount, uint256 timestamp);
    event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);
    event DisputeRaised(address indexed initiator, uint256 timestamp);
    event ContractTerminated(address indexed initiator, uint256 timestamp);

    // ── State machine (minimum 5 states, MANDATORY) ──────────────────────
    enum ContractState { Created, Active, Completed, Disputed, Terminated }

    // ── Constants (encode effective date, FIX G) ─────────────────────────
    uint256 public constant EFFECTIVE_DATE = 1618099200; // 2021-04-11
    // Governing law: <jurisdiction>

    // ── State variables ──────────────────────────────────────────────────
    ContractState private _state;
    address payable private _partyA;
    address payable private _partyB;
    address private _arbitrator;          // MANDATORY (SEC-009, COV-041)
    uint256 private _amount;
    uint256 private _deadline;
    uint256 private _penaltyRate;         // basis points e.g. 500 = 5%
    bool private _locked;                 // reentrancy guard — NEVER a param

    // ── Modifiers ────────────────────────────────────────────────────────
    modifier onlyParties() {
        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();
        _;
    }
    modifier onlyPartyA() {
        if (msg.sender != _partyA) revert Unauthorized();
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

    // ── Reentrancy guard (ZERO params, reads _locked directly) ───────────
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }

    // ── Constructor (initialise ALL vars) ────────────────────────────────
    constructor(
        address payable partyA_,
        address payable partyB_,
        address arbitrator_,
        uint256 amount_,
        uint256 deadlineDays_,
        uint256 penaltyRateBps_
    ) payable {
        _partyA      = partyA_;
        _partyB      = partyB_;
        _arbitrator  = arbitrator_;
        _amount      = amount_;
        _deadline    = block.timestamp + (deadlineDays_ * 1 days);
        _penaltyRate = penaltyRateBps_;
        _state       = ContractState.Created;
        emit ContractCreated(partyA_, partyB_, amount_);
    }

    // ── Payment (payable, reentrancy guarded) ────────────────────────────
    /// @notice Sends payment to partyB upon contract activation.
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

    // ── Delivery acknowledgement (MANDATORY, FIX C) ──────────────────────
    /// @notice PartyA acknowledges receipt of deliverables, completing the contract.
    function acknowledgeDelivery()
        external
        onlyPartyA
        inState(ContractState.Active)
    {
        _state = ContractState.Completed;
        (bool ok,) = _partyB.call{value: address(this).balance}("");
        if (!ok) revert InsufficientPayment(0, address(this).balance);
        emit DeliveryAcknowledged(msg.sender, block.timestamp);
    }

    // ── Dispute (MANDATORY — FIX A) ──────────────────────────────────────
    /// @notice Raises a dispute, freezing the contract pending arbitration.
    function dispute()
        external
        onlyParties
    {
        if (_state == ContractState.Disputed) revert AlreadyDisputed();
        if (_state == ContractState.Completed || _state == ContractState.Terminated)
            revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Disputed;
        emit DisputeRaised(msg.sender, block.timestamp);
    }

    // ── Terminate ────────────────────────────────────────────────────────
    /// @notice Terminates the contract. Both parties may call this.
    function terminate() external onlyParties noReentrant {
        if (_state == ContractState.Terminated) revert InvalidState(uint8(_state), uint8(ContractState.Active));
        _state = ContractState.Terminated;
        if (address(this).balance > 0) {
            (bool ok,) = _partyA.call{value: address(this).balance}("");
            if (!ok) revert InsufficientPayment(0, address(this).balance);
        }
        emit ContractTerminated(msg.sender, block.timestamp);
    }

    // ── Penalty (apply if deadline missed) ───────────────────────────────
    /// @notice Calculates late-delivery penalty in wei.
    /// @param baseAmount The original payment amount in wei.
    /// @return penaltyWei Penalty amount in wei.
    function calculatePenalty(uint256 baseAmount) public view returns (uint256 penaltyWei) {
        if (block.timestamp <= _deadline) return 0;
        uint256 daysLate = (block.timestamp - _deadline) / 1 days;
        penaltyWei = (baseAmount * _penaltyRate * daysLate) / 10_000;
    }

    // ── View state (no mappings in return tuple, FIX 3) ──────────────────
    /// @notice Returns all key contract state fields.
    /// @return partyA_     Address of party A.
    /// @return partyB_     Address of party B.
    /// @return arbitrator_ Arbitrator address.
    /// @return state_      Current ContractState cast to uint8.
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

    // ── Accept ETH ───────────────────────────────────────────────────────
    receive() external payable {}
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
- Before finishing, mentally scan for: (1) any require() with strings, (2) missing
  dispute() function, (3) missing enum ContractState, (4) missing @notice on functions,
  (5) missing _arbitrator state variable. Fix all before outputting.

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
        f"  ↳ ENCODE THIS  : Store as `uint256 public constant EFFECTIVE_DATE`",
        f"    in the contract with the nearest unix timestamp + a comment.",
        f"EXPIRY DATE      : {doc.expiry_date or 'Not specified'}",
        f"GOVERNING LAW    : {doc.governing_law or 'Not specified'}",
        f"  ↳ ADD COMMENT  : // Governing law: {doc.governing_law or 'Not specified'}",
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
        "CONVERSION REQUIREMENTS (ALL are mandatory — not optional):",
        "",
        "STRUCTURE:",
        "1.  Contract name: derive a clean PascalCase name that includes the words",
        "    from the title (e.g. AgreementAndPlanOfMergerContract).",
        "2.  Encode EVERY clause as on-chain logic, state, events, or modifiers.",
        "",
        "CLAUSE ENCODING MAP:",
        "3.  Payment clauses    → payable function + exact wei validation + noReentrant.",
        "4.  Penalty clauses    → calculatePenalty() function with _penaltyRate in bps.",
        "5.  Expiry/term clauses → _deadline = block.timestamp + N days in constructor.",
        "6.  Obligation clauses → acknowledgeDelivery() or confirmMilestone() function",
        "                         that transitions ContractState.Active → Completed.",
        "7.  Dispute clauses    → dispute() function (CRITICAL — see rule 21 above).",
        "                         + _arbitrator address state variable (see rule 21).",
        "                         + DisputeRaised event emitted inside dispute().",
        "8.  Confidentiality/IP → acknowledgement events + bool flags.",
        "",
        "MANDATORY FUNCTIONS (all must be present):",
        "9.  dispute()              — raises dispute, sets state, emits DisputeRaised.",
        "10. acknowledgeDelivery()  — or confirmMilestone() — confirms completion.",
        "11. calculatePenalty()     — returns penalty wei for late delivery.",
        "12. terminate()            — accessible by both parties.",
        "13. getContractState()     — returns tuple of scalar values (NO mappings).",
        "                            Must include _arbitrator in the return tuple.",
        "",
        "MANDATORY STATE VARIABLES:",
        "14. address private _arbitrator  — set in constructor, returned by getContractState.",
        "15. enum ContractState { Created, Active, Completed, Disputed, Terminated }",
        "16. uint256 public constant EFFECTIVE_DATE = <unix_timestamp>; // human date",
        "",
        "QUALITY GATES:",
        "17. Minimum 5 `/// @notice` NatSpec comments — one per public/external function.",
        "18. Zero require() calls with string messages — use custom errors everywhere.",
        "19. Minimum 5 events declared and emitted.",
        "20. Reentrancy guard on every function that transfers ETH.",
        "21. DO NOT use SafeMath — 0.8.x has built-in overflow protection.",
        "22. DO NOT import OpenZeppelin — standalone contract only.",
        "",
        "SELF-CHECK before outputting (scan line by line):",
        "  □ Is there a dispute() function?",
        "  □ Is there an _arbitrator state variable?",
        "  □ Is there a DisputeRaised event?",
        "  □ Is there an enum ContractState with ≥5 states?",
        "  □ Is there an acknowledgeDelivery() or confirmMilestone()?",
        "  □ Is there a uint256 public constant EFFECTIVE_DATE?",
        "  □ Are there zero require(condition, \"string\") calls?",
        "  □ Are there ≥5 /// @notice comments?",
        "  □ Are braces balanced?",
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
    Extended to catch all failures observed in results.json.
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

6.  ZERO require() calls with string arguments anywhere in the file.
    Search every line for `require(` — each one MUST be replaced:
        WRONG: require(condition, "message");
        RIGHT: if (!condition) revert CustomError();
    This check has zero tolerance. Even ONE require-string is a failure.

7.  Every state-changing function emits an event.

8.  NatSpec: minimum 5 `/// @notice` comments — one on every public/external function.
    If fewer than 5 are found, add them.

9.  SPDX-License-Identifier is the very first line of the file.

10. pragma solidity ^0.8.16 is the second line.

11. getContractState() return tuple contains NO mapping types.
    If a mapping appears in the return list, remove it and add a separate getter.

12. Braces are balanced (no truncation). Count {{ and }} — they must be equal.

13. No OpenZeppelin imports, no SafeMath.

14. CRITICAL — dispute() function MUST be present:
    - Sets _state = ContractState.Disputed (or equivalent).
    - Emits a DisputeRaised (or equivalent dispute) event.
    - Reverts with Unauthorized() if caller is not a recognised party.
    If absent, ADD it now.

15. CRITICAL — _arbitrator address state variable MUST be declared and initialised
    in the constructor. If absent, ADD it and wire it into getContractState().

16. CRITICAL — enum ContractState with at least 5 states MUST be present.
    States: Created, Active, Completed, Disputed, Terminated (or equivalent).
    If absent, ADD the enum and update all transition functions.

17. acknowledgeDelivery() or confirmMilestone() function MUST be present.
    It must transition state to Completed and emit a completion event.
    If absent, ADD it.

18. uint256 public constant EFFECTIVE_DATE MUST be declared (even if approximate).
    If absent, ADD it with a best-estimate unix timestamp and a date comment.

If ANY issue is found: output the COMPLETE corrected Solidity source only (no explanation).
If the contract passes all checks: output the EXACT same code unchanged.

CONTRACT TO AUDIT:
{solidity_code}
"""


# Alias for callers that prefer the "get_" naming style
get_validation_prompt = build_validation_prompt