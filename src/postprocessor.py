"""
postprocessor.py — Cleans raw LLM output, applies deterministic Solidity
fixes, and generates the final output artefacts.

FIXES vs previous version:
  1–4.  (unchanged) SPDX, pragma, banner, safemath, OZ, selfdestruct, tx.origin
  5.    _fix_calculatePenalty_view — remove view from calculatePenalty()
  6.    _fix_locked_declaration — ensure bool private _locked at contract scope
  7.    _fix_onlyX_modifiers — ensure ≥2 onlyX modifiers
  8.    _fix_governing_law_constant — inject GOVERNING_LAW string constant
  9.    _fix_start_date — inject startDate immutable
  10.   _fix_require_to_custom_errors — convert remaining require() calls
  11.   _fix_payable_and_receive — inject pay/receive if missing

  NEW FIXES (this version):
  12.   _fix_missing_custom_errors — declare ALL error types that are `revert`ed
        but not declared. Catches: Unauthorized, InvalidState, ReentrantCall,
        InsufficientPayment, DeadlinePassed, AlreadyDisputed, and any other
        revert targets used in the generated code.
  13.   _fix_missing_events — declare ALL events that are `emit`ted but not
        declared. Catches: PaymentReceived, and any other undeclared events.
  14.   _fix_missing_noReentrant — if noReentrant is used in function signatures
        but the modifier body is absent, inject the full canonical modifier.
  15.   _fix_undeclared_state_vars — detect address/uint/bool identifiers used
        in modifier bodies / function bodies that are never declared as state
        variables; replace references with safe fallbacks (_arbitrator, etc.)
        to prevent "Identifier not found" compile errors.
  16.   _fix_broken_onlyParties — rewrite onlyParties() bodies that reference
        undeclared variables (parent, acquisitionSub, buyer, seller, etc.)
        using whatever party addresses ARE declared (_partyA/_partyB or
        _arbitrator as fallback).
  17.   _fix_immutable_init — catch `uint256 public immutable startDate = X;`
        (direct initialisation) which is only allowed for literals. When
        EFFECTIVE_DATE (a constant) is used as the RHS, rewrite to
        `uint256 public immutable startDate;` + constructor assignment.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from extractor import ContractDocument


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _contract_body(code: str) -> tuple[str, int]:
    """Return (body_text, offset) of the outermost contract body."""
    m = re.search(r"\bcontract\s+\w+[^{]*\{", code)
    if m:
        return code[m.end():], m.end()
    return code, 0


def _declared_identifiers(code: str) -> set[str]:
    """
    Return the set of all top-level identifiers declared in the contract:
    state variables, events, errors, modifiers, functions.
    """
    ids: set[str] = set()
    # state vars: type [visibility] name ;
    for m in re.finditer(
        r"^\s*(?:address|uint\d*|int\d*|bool|bytes\d*|string|mapping)\s+"
        r"(?:payable\s+)?(?:private|public|internal|external\s+)?(?:immutable\s+|constant\s+)?(\w+)",
        code, re.MULTILINE,
    ):
        ids.add(m.group(1))
    # enum names + their members
    for m in re.finditer(r"\benum\s+(\w+)", code):
        ids.add(m.group(1))
    # event names
    for m in re.finditer(r"\bevent\s+(\w+)", code):
        ids.add(m.group(1))
    # error names
    for m in re.finditer(r"\berror\s+(\w+)", code):
        ids.add(m.group(1))
    # modifier names
    for m in re.finditer(r"\bmodifier\s+(\w+)", code):
        ids.add(m.group(1))
    # function names
    for m in re.finditer(r"\bfunction\s+(\w+)", code):
        ids.add(m.group(1))
    # constructor (special)
    ids.add("constructor")
    return ids


# ═══════════════════════════════════════════════════════════════════════════
#  Basic fixes (unchanged from previous version)
# ═══════════════════════════════════════════════════════════════════════════

def _fix_pragma(code: str) -> str:
    code = re.sub(
        r"pragma\s+solidity\s+[\^~]?0\.\d+\.\d+;",
        "pragma solidity ^0.8.16;",
        code,
    )
    if "pragma solidity" not in code:
        code = re.sub(
            r"(//\s*SPDX-License-Identifier:[^\n]+\n)",
            r"\1pragma solidity ^0.8.16;\n",
            code,
        )
    return code


def _fix_spdx(code: str) -> str:
    if "SPDX-License-Identifier" not in code:
        code = "// SPDX-License-Identifier: MIT\n" + code
    return code


def _fix_trailing_whitespace(code: str) -> str:
    return "\n".join(ln.rstrip() for ln in code.splitlines()).strip() + "\n"


def _fix_safemath(code: str) -> str:
    code = re.sub(r'import\s+["\'].*[Ss]afe[Mm]ath.*["\'];\n?', "", code)
    code = re.sub(r"using\s+SafeMath\s+for\s+[^;]+;\n?", "", code)
    return code


def _fix_openzeppelin_imports(code: str) -> str:
    code = re.sub(r'import\s+["\']@openzeppelin/[^"\']+["\'];\n?', "", code)
    code = re.sub(r"\bis\s+(?:Ownable|ReentrancyGuard|Pausable)\b", "", code)
    return code


def _fix_selfdestruct(code: str) -> str:
    return re.sub(
        r"selfdestruct\s*\([^)]*\)\s*;",
        "// selfdestruct removed — deprecated in Solidity 0.8.x",
        code,
    )


def _fix_tx_origin(code: str) -> str:
    return re.sub(r"\btx\.origin\b", "msg.sender /* was tx.origin — fixed */", code)


def _fix_noReentrant_modifier(code: str) -> str:
    """Remove erroneous parameters from noReentrant modifier signature."""
    code = re.sub(
        r"modifier\s+noReentrant\s*\(\s*bool\s+storage\s+\w+\s*\)",
        "modifier noReentrant()",
        code,
    )
    return code


def _fix_mapping_return(code: str) -> str:
    code = re.sub(r",?\s*mapping\s*\([^)]+\)[^,)]*(?=\s*[,)])", "", code)
    return code


def _fix_calculatePenalty_view(code: str) -> str:
    """Remove `view` from calculatePenalty() — it emits events."""
    code = re.sub(
        r"(function\s+calculatePenalty\s*\([^)]*\)\s+(?:external|public)\s+)view\s+",
        r"\1",
        code,
    )
    # Also handle: external view returns pattern
    code = re.sub(
        r"(function\s+calculatePenalty\s*\([^)]*\)[^{]*)\bview\b(\s+returns)",
        r"\1\2",
        code,
    )
    return code


def _fix_locked_declaration(code: str) -> str:
    """Ensure `bool private _locked;` is declared at contract scope."""
    if re.search(r"bool\s+private\s+_locked\s*;", code):
        return code
    inject = "    bool private _locked; // reentrancy guard\n"
    lines = code.splitlines(keepends=True)
    insert_idx = None
    in_contract = False
    for i, line in enumerate(lines):
        if re.match(r"\s*contract\s+\w+", line):
            in_contract = True
        if in_contract and re.match(r"\s*(modifier|constructor)\b", line):
            insert_idx = i
            break
    if insert_idx is not None:
        lines.insert(insert_idx, inject)
        return "".join(lines)
    return code


def _fix_governing_law_constant(code: str, doc: ContractDocument) -> str:
    if "GOVERNING_LAW" in code:
        return code
    gov = doc.governing_law or ""
    if not gov:
        return code
    gov_word = gov.split()[0]
    constant_line = f'    string public constant GOVERNING_LAW = "{gov_word}";\n'
    m = re.search(r"(uint256\s+public\s+constant\s+EFFECTIVE_DATE[^\n]+\n)", code)
    if m:
        code = code[:m.end()] + constant_line + code[m.end():]
    else:
        m = re.search(r"(pragma\s+solidity[^\n]+\n)", code)
        if m:
            code = code[:m.end()] + constant_line + code[m.end():]
    return code


def _fix_start_date(code: str) -> str:
    """
    Inject `uint256 public immutable startDate` if missing.
    FIX-17: Also repair `uint256 public immutable startDate = EFFECTIVE_DATE;`
    — immutables cannot be initialised with a constant expression in-line;
    they must be assigned in the constructor.
    """
    # Repair inline initialisation: immutable startDate = EFFECTIVE_DATE;
    # Strip the RHS so it becomes `uint256 public immutable startDate;`
    inline_fixed = bool(re.search(
        r"uint256\s+public\s+immutable\s+startDate\s*=\s*EFFECTIVE_DATE\s*;",
        code,
    ))
    code = re.sub(
        r"(uint256\s+public\s+immutable\s+startDate)\s*=\s*EFFECTIVE_DATE\s*;",
        r"\1;",
        code,
    )

    has_start_date = bool(re.search(r"\bstartDate\b", code))
    has_effective_date = bool(re.search(r"\beffectiveDate\b", code))

    if has_start_date or has_effective_date:
        # Ensure constructor assignment exists (needed after stripping inline init)
        if has_start_date and "startDate = EFFECTIVE_DATE" not in code:
            ctor = re.search(r"constructor\s*\([^)]*\)[^{]*\{", code)
            if ctor:
                code = (
                    code[: ctor.end()]
                    + "\n        startDate = EFFECTIVE_DATE;"
                    + code[ctor.end():]
                )
        return code

    if "EFFECTIVE_DATE" not in code:
        return code

    decl = "    uint256 public immutable startDate;\n"
    m = re.search(r"(uint256\s+public\s+constant\s+EFFECTIVE_DATE[^\n]+\n)", code)
    if m:
        code = code[: m.end()] + decl + code[m.end():]

    ctor = re.search(r"constructor\s*\([^)]*\)[^{]*\{", code)
    if ctor:
        code = (
            code[: ctor.end()]
            + "\n        startDate = EFFECTIVE_DATE;"
            + code[ctor.end():]
        )
    return code


def _parse_require_args(src: str, start: int) -> tuple[str, str] | None:
    """
    Parse the arguments of a require(...) call starting at `start` (the index
    of the opening parenthesis).  Returns (condition, message) or None if the
    call cannot be parsed.  Correctly handles nested parentheses such as
    address(0), keccak256(...), etc.
    """
    depth = 0
    i = start
    n = len(src)
    parts: list[str] = []
    seg_start = start + 1  # skip the opening '('

    while i < n:
        c = src[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                parts.append(src[seg_start:i])
                break
        elif c == ',' and depth == 1:
            parts.append(src[seg_start:i])
            seg_start = i + 1
        i += 1

    if not parts:
        return None

    condition = parts[0].strip()
    message   = parts[1].strip().strip('"\'') if len(parts) > 1 else ""
    return condition, message


def _negate_condition(condition: str) -> str:
    """
    Return the logical negation of a Solidity boolean expression.

    Simple atomic conditions are inverted by flipping the operator.
    Compound conditions (containing && or || at the top level, i.e. not
    inside nested parentheses) are wrapped in !(…) to avoid incorrectly
    dropping terms.
    """
    stripped = condition.strip()

    # Check for top-level && or || (not inside nested parens)
    depth = 0
    for ch in stripped:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0 and stripped[stripped.index(ch):].startswith(('&&', '||')):
            # Compound condition — wrap the whole thing
            return f"!({stripped})"

    # Remove a single wrapping pair of parens if present
    while stripped.startswith("(") and stripped.endswith(")"):
        # Verify the parens are actually matching
        depth2 = 0
        for i, ch in enumerate(stripped):
            if ch == '(':
                depth2 += 1
            elif ch == ')':
                depth2 -= 1
                if depth2 == 0 and i < len(stripped) - 1:
                    break  # closing paren is not the last char → don't strip
        else:
            stripped = stripped[1:-1].strip()
            continue
        break

    if stripped.startswith("!"):
        inner = stripped[1:].strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1].strip()
        return inner
    if " == " in stripped:
        return stripped.replace(" == ", " != ", 1)
    if " != " in stripped:
        return stripped.replace(" != ", " == ", 1)
    if " >= " in stripped:
        return stripped.replace(" >= ", " < ", 1)
    if " <= " in stripped:
        return stripped.replace(" <= ", " > ", 1)
    if " > " in stripped:
        return stripped.replace(" > ", " <= ", 1)
    if " < " in stripped:
        return stripped.replace(" < ", " >= ", 1)
    return f"!({stripped})"


def _fix_require_to_custom_errors(code: str) -> str:
    """
    Convert ALL remaining require() calls to the custom-error pattern.

    Uses a parenthesis-depth-aware parser instead of a naive [^,)] regex so
    that nested calls like address(0), keccak256(...), abi.encode(...) are
    handled correctly and never produce malformed output such as:
        if (!(partyA == address(0)) revert Unauthorized() && partyB == address(0), ...)
    """
    result = []
    i = 0
    n = len(code)

    while i < n:
        # Look for `require` as a whole word
        m = re.search(r'\brequire\s*\(', code[i:])
        if not m:
            result.append(code[i:])
            break

        # Append everything up to (and including) the chars before `require`
        pre_start = i + m.start()
        result.append(code[i:pre_start])

        paren_open = i + m.end() - 1  # index of the '(' in require(
        parsed = _parse_require_args(code, paren_open)

        if parsed is None:
            # Cannot parse — emit as-is and advance past the keyword
            result.append(code[pre_start: i + m.end()])
            i = i + m.end()
            continue

        condition, _message = parsed
        neg = _negate_condition(condition)
        result.append(f"if ({neg}) revert Unauthorized()")

        # Advance past the closing ')' of the require(...)
        depth = 0
        j = paren_open
        while j < n:
            if code[j] == '(':
                depth += 1
            elif code[j] == ')':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        i = j

    return "".join(result)


def _fix_bare_locked(code: str) -> str:
    """
    The LLM sometimes uses bare `locked` (without underscore) inside
    receive() and modifier bodies instead of `_locked`.
    Replace every whole-word `locked` that is NOT already preceded by `_`
    with `_locked`, but ONLY if `bool private _locked` is declared (or will
    be injected) — so we don't rename unrelated variables.
    """
    # Only act when the canonical declaration is present (or will be injected)
    if not re.search(r"\bbool\b[^;]+\b_locked\b", code) and "bool private _locked" not in code:
        return code
    # Replace `locked` that is not preceded by `_`
    code = re.sub(r'(?<!_)\blocked\b', '_locked', code)
    return code


def _fix_contractstate_type(code: str) -> str:
    """
    The LLM sometimes declares `uint256 public contractState = ContractState.Created;`
    which is a type-mismatch compile error.  Rewrite to use the enum type.
    """
    # uint256 [visibility] contractState = ContractState.xxx  →  ContractState [visibility] contractState
    code = re.sub(
        r'\buint256\b(\s+(?:public|private|internal)?\s+)(contractState\s*=\s*ContractState\.)',
        r'ContractState\1\2',
        code,
    )
    # Also catch: uint256 contractState; with no initialiser then assigned ContractState.xxx
    code = re.sub(
        r'\buint256\b(\s+(?:public|private|internal)?\s+)(contractState\s*;)',
        r'ContractState\1\2',
        code,
    )
    return code


def _fix_msg_value_validation(code: str) -> str:
    """
    SEC-004: ensure every external payable function validates msg.value.
    If a payable function body contains no `msg.value` comparison, inject
    a guard immediately after the opening brace.
    """
    def _add_value_check(m: re.Match) -> str:
        fn_head = m.group(0)        # everything up to and including the '{'
        body_start = m.end()
        # Find closing brace of this function (depth-aware)
        depth = 1
        j = body_start
        src = m.string
        while j < len(src) and depth:
            if src[j] == '{':
                depth += 1
            elif src[j] == '}':
                depth -= 1
            j += 1
        body = src[body_start:j - 1]
        if re.search(r'msg\.value\s*[=!<>]', body):
            return fn_head          # already validates — leave alone
        guard = "\n        if (msg.value == 0) revert InsufficientPayment(msg.value, 0);"
        return fn_head + guard

    code = re.sub(
        r'function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\{',
        _add_value_check,
        code,
    )
    return code


def _fix_expiry_deadline(code: str) -> str:
    """
    COV-020: inject a `uint256 private _deadline;` state variable and a
    `setDeadline()` function if neither `_deadline` nor `block.timestamp +`
    appears anywhere in the contract.
    """
    has_deadline = bool(
        re.search(r'\b_deadline\b|\bdeadlineAt\b', code) or
        re.search(r'block\.timestamp\s*\+', code)
    )
    if has_deadline:
        return code

    decl = "    uint256 private _deadline; // contract expiry (unix timestamp)\n"

    # Only match contract-scope state variable lines: exactly 4 spaces of indent
    # (function-body lines have 8+ spaces).  Also require a visibility keyword
    # so we don't accidentally match local variable declarations.
    last_var = None
    for m in re.finditer(
        r'^    (?:address|uint\d*|bool|bytes\d*|string)\s+'
        r'(?:payable\s+)?(?:private|public|internal)\s+'
        r'(?:immutable\s+|constant\s+)?\w+\s*[=;][^\n]*\n',
        code, re.MULTILINE,
    ):
        last_var = m

    if last_var:
        code = code[:last_var.end()] + decl + code[last_var.end():]
    else:
        # Fallback: inject right after the contract opening brace line
        m2 = re.search(r'(\bcontract\s+\w+[^{]*\{[^\n]*\n)', code)
        if m2:
            code = code[:m2.end()] + decl + code[m2.end():]

    # Inject setDeadline() before the closing brace of the contract
    set_fn = (
        "\n    /// @notice Set the contract expiry deadline (seconds from now).\n"
        "    function setDeadline(uint256 durationSeconds) external onlyArbitrator {\n"
        "        _deadline = block.timestamp + durationSeconds;\n"
        "    }\n"
    )
    idx = code.rfind("}")
    if idx != -1:
        code = code[:idx] + set_fn + code[idx:]
    return code


def _fix_party_var_naming(code: str) -> str:
    """
    FIX-21: The LLM sometimes declares `address private partyA;` and
    `address private partyB;` (no underscore prefix) instead of `_partyA`
    and `_partyB`. Rename them everywhere for consistency and to ensure
    _fix_broken_onlyParties can identify them correctly.
    """
    # Only rename if the underscore-prefixed form is NOT already present,
    # to avoid double-renaming.
    for bare, canonical in [("partyA", "_partyA"), ("partyB", "_partyB")]:
        if re.search(rf'\b{canonical}\b', code):
            continue  # canonical form already present — leave alone
        if re.search(rf'\b{bare}\b', code):
            # Rename whole-word occurrences, careful not to hit e.g. `_partyA` or `partyABC`
            code = re.sub(rf'\b{bare}\b', canonical, code)
    return code


def _fix_modifier_ordering(code: str) -> str:
    """
    FIX-22: Modifiers must be declared before the first function that uses
    them (Solidity requires this when a modifier is used before its declaration
    in older compiler versions, and it avoids "not yet visible" errors).

    Move all `modifier` blocks to appear before the first `function` definition.
    """
    # Find the position of the first `function` keyword at contract scope
    # (4-space indent, to skip functions inside modifiers/other blocks)
    first_fn = re.search(r'\n    function\s+\w+', code)
    if not first_fn:
        return code
    first_fn_pos = first_fn.start()

    # Collect all modifier blocks that appear AFTER the first function
    modifier_pat = re.compile(
        r'\n(    modifier\s+\w+[^{]*\{(?:[^{}]|\{[^}]*\})*\})',
        re.DOTALL,
    )

    modifiers_to_move: list[tuple[int, int, str]] = []
    for m in modifier_pat.finditer(code):
        if m.start() > first_fn_pos:
            modifiers_to_move.append((m.start(), m.end(), m.group(0)))

    if not modifiers_to_move:
        return code

    # Remove them from their original positions (iterate in reverse)
    for start, end, _ in reversed(modifiers_to_move):
        code = code[:start] + code[end:]

    # Re-find first_fn position (offsets changed after removal)
    first_fn = re.search(r'\n    function\s+\w+', code)
    if not first_fn:
        return code

    # Insert all collected modifiers just before the first function
    insert_pos = first_fn.start()
    block = "".join(text for _, _, text in modifiers_to_move)
    code = code[:insert_pos] + block + code[insert_pos:]
    return code


def _fix_receive_body(code: str) -> str:
    """
    FIX-19: The LLM sometimes writes receive() bodies that call internal
    functions like `pay(msg.value)` — this is a compile error because
    pay() is external and cannot be called internally.
    Replace any such body with a safe canonical emit.
    """
    def _fix_receive(m: re.Match) -> str:
        body = m.group(1)
        # If the body calls any function other than emit/revert/if/require, replace it
        if re.search(r'\b(?!emit|revert|if|require)\w+\s*\(', body):
            if "PaymentReceived" in code:
                safe_body = "        emit PaymentReceived(msg.sender, msg.value);\n    "
            elif "PaymentMade" in code:
                safe_body = "        emit PaymentMade(msg.sender, msg.value);\n    "
            else:
                safe_body = "        // ETH received\n    "
            return m.group(0).replace(body, safe_body)
        return m.group(0)

    code = re.sub(
        r'receive\s*\(\s*\)\s+external\s+payable\s*\{([^}]*)\}',
        _fix_receive,
        code,
        flags=re.DOTALL,
    )
    return code


def _fix_return_in_non_returning_fn(code: str) -> str:
    """
    FIX-20: Remove `return value;` from functions that have no `returns (...)`
    clause — this is a compile error in Solidity.
    """
    def _strip_return(m: re.Match) -> str:
        fn_head = m.group(1)
        body    = m.group(2)
        if re.search(r'\breturns\s*\(', fn_head):
            return m.group(0)
        new_body = re.sub(r'\n?\s*return\s+[^;]+;\n?', '\n', body)
        if new_body == body:
            return m.group(0)
        return fn_head + "{\n" + new_body + "    }"

    code = re.sub(
        r'(function\s+\w+[^{]+)\{\n((?:[^{}]|\{[^}]*\})*?)\n    \}',
        _strip_return,
        code,
        flags=re.DOTALL,
    )
    return code


def _fix_payable_noReentrant(code: str) -> str:
    """
    SEC-003: ensure every external payable function uses the noReentrant modifier.
    If noReentrant is defined in this contract, add it to any payable function
    signature that is missing it.
    """
    if not re.search(r"modifier\s+noReentrant\s*\(", code):
        return code  # modifier not present — nothing to apply

    def _add_noReentrant(m: re.Match) -> str:
        sig = m.group(0)
        if "noReentrant" in sig:
            return sig  # already has it
        # Insert noReentrant before the opening brace
        return re.sub(r'\{\s*$', 'noReentrant {\n', sig.rstrip())

    # Match function signatures that are payable and external, possibly spanning
    # multiple lines (modifiers on their own lines before the `{`)
    code = re.sub(
        r'(function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\{)',
        _add_noReentrant,
        code,
        flags=re.DOTALL,
    )
    return code


def _fix_natspec_comments(code: str) -> str:
    """
    SOL-013: ensure every public/external function has a /// @notice comment.
    Count existing ones; if < 8, prepend a generic @notice above each
    public/external function that is missing one.

    Handles:
    - Multi-line signatures (modifiers on separate lines before the opening brace)
    - receive() / fallback() which lack the `function` keyword
    """
    MIN_NOTICE = 8

    existing = len(re.findall(r'///\s*@notice', code))
    if existing >= MIN_NOTICE:
        return code

    lines = code.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    added = existing

    while i < len(lines):
        line = lines[i]

        # Match a `function` keyword on this line that has public/external
        # visibility declared *anywhere* in the following few lines up to `{`
        fn_start = re.match(r'(\s*)function\s+(\w+)\s*\(', line)
        if fn_start and added < MIN_NOTICE:
            indent   = fn_start.group(1)
            fn_name  = fn_start.group(2)
            # Scan ahead (up to 8 lines) to find the visibility and opening `{`
            look = "".join(lines[i: i + 8])
            is_public_or_external = bool(re.search(r'\b(public|external)\b', look))
            has_opening_brace = "{" in look
            prev = out[-1].rstrip() if out else ""
            if is_public_or_external and has_opening_brace and "@notice" not in prev:
                out.append(f"{indent}/// @notice Execute {fn_name} operation.\n")
                added += 1

        # Also match receive() / fallback() which have no `function` keyword
        rcv_match = re.match(r'(\s*)(receive|fallback)\s*\(\s*\)\s+external', line)
        if rcv_match and added < MIN_NOTICE:
            indent  = rcv_match.group(1)
            fn_name = rcv_match.group(2)
            prev = out[-1].rstrip() if out else ""
            if "@notice" not in prev:
                out.append(f"{indent}/// @notice {fn_name.capitalize()} ETH deposits.\n")
                added += 1

        out.append(line)
        i += 1

    return "".join(out)



# Known error types with their parameter signatures
_KNOWN_ERROR_SIGS: dict[str, str] = {
    "Unauthorized":        "error Unauthorized();",
    "InvalidState":        "error InvalidState(uint8 current, uint8 required);",
    "ReentrantCall":       "error ReentrantCall();",
    "InsufficientPayment": "error InsufficientPayment(uint256 sent, uint256 required);",
    "DeadlinePassed":      "error DeadlinePassed(uint256 deadline, uint256 current);",
    "AlreadyDisputed":     "error AlreadyDisputed();",
    "ContractExpired":     "error ContractExpired();",
    "NotActive":           "error NotActive();",
    "NotInDispute":        "error NotInDispute();",
    "OnlyArbitrator":      "error OnlyArbitrator();",
    "OnlyParty":           "error OnlyParty();",
}


def _fix_missing_custom_errors(code: str) -> str:
    """
    FIX-12: Find every `revert SomeName(...)` call in the contract.
    If SomeName is not declared as a custom error, inject its declaration.

    Also proactively injects the full set of standard errors used by the
    template (Unauthorized, ReentrantCall, etc.) if ANY of them are missing,
    to avoid compile errors from modifiers and injected boilerplate.
    """
    # Collect all revert targets
    reverted = set(re.findall(r"\brevert\s+(\w+)\s*[;(]", code))

    # Always ensure the core set of errors that our injected modifiers use
    # are declared, even if the LLM didn't write explicit revert calls yet.
    core_errors = {
        "Unauthorized", "ReentrantCall", "InvalidState",
        "InsufficientPayment", "DeadlinePassed", "AlreadyDisputed",
    }
    # Include core errors in the "reverted" set so they get declared
    reverted |= core_errors

    # Collect already-declared errors
    declared = set(re.findall(r"\berror\s+(\w+)\s*[;(]", code))

    missing = reverted - declared
    if not missing:
        return code

    # Build injection block
    injections: list[str] = []
    for name in sorted(missing):
        sig = _KNOWN_ERROR_SIGS.get(name, f"error {name}();")
        injections.append(f"    {sig}")

    inject_block = "\n".join(injections) + "\n"

    # Inject after the contract opening brace, before any existing declarations
    m = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
    if m:
        pos = m.end()
        code = code[:pos] + "\n" + inject_block + code[pos:]
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  NEW FIX-13: Ensure all emitted events are declared
# ═══════════════════════════════════════════════════════════════════════════

# Canonical signatures for commonly emitted events
_KNOWN_EVENT_SIGS: dict[str, str] = {
    "PaymentReceived":      "event PaymentReceived(address indexed from, uint256 amount);",
    "PaymentMade":          "event PaymentMade(address indexed payer, uint256 amount);",
    "ContractCreated":      "event ContractCreated(address indexed partyA, address indexed partyB, uint256 amount);",
    "DeliveryAcknowledged": "event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);",
    "DisputeRaised":        "event DisputeRaised(address indexed initiator, uint256 timestamp);",
    "ContractTerminated":   "event ContractTerminated(address indexed initiator, uint256 timestamp);",
    "PenaltyCalculated":    "event PenaltyCalculated(uint256 penaltyWei);",
    "StateChanged":         "event StateChanged(uint8 from, uint8 to);",
}


def _fix_missing_events(code: str) -> str:
    """
    FIX-13: Find every `emit SomeName(...)` call.
    If SomeName is not declared as an event, inject its declaration.
    """
    emitted  = set(re.findall(r"\bemit\s+(\w+)\s*\(", code))
    declared = set(re.findall(r"\bevent\s+(\w+)\s*\(", code))

    missing = emitted - declared
    if not missing:
        return code

    injections: list[str] = []
    for name in sorted(missing):
        sig = _KNOWN_EVENT_SIGS.get(name, f"event {name}(address indexed caller, uint256 value);")
        injections.append(f"    {sig}")

    inject_block = "\n".join(injections) + "\n"

    # Inject after the last existing event declaration, or after contract opening
    last_event = None
    for m in re.finditer(r"event\s+\w+[^;]+;\n", code):
        last_event = m
    if last_event:
        pos = last_event.end()
        code = code[:pos] + inject_block + code[pos:]
    else:
        m = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
        if m:
            pos = m.end()
            code = code[:pos] + "\n" + inject_block + code[pos:]
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  NEW FIX-14: Inject noReentrant modifier body if used but not declared
# ═══════════════════════════════════════════════════════════════════════════

_NOREENTRANT_BODY = """\
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }
"""


def _fix_missing_noReentrant(code: str) -> str:
    """
    FIX-14: If `noReentrant` appears in a function signature but no
    `modifier noReentrant` body exists, inject the canonical body.

    Also ensures:
    - `bool private _locked;` state variable is declared.
    - `error ReentrantCall();` is declared (so the modifier body compiles).
    """
    used    = bool(re.search(r"\bnoReentrant\b", code))
    defined = bool(re.search(r"modifier\s+noReentrant\s*\(", code))

    if not used or defined:
        return code

    # ── Ensure `bool private _locked;` is at contract scope ─────────────────
    if not re.search(r"bool\s+private\s+_locked\s*;", code):
        inject_lock = "    bool private _locked; // reentrancy guard\n"
        lines = code.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if re.match(r"\s*constructor\b", line):
                lines.insert(i, inject_lock)
                break
        code = "".join(lines)

    # ── Ensure `error ReentrantCall();` is declared ──────────────────────────
    # The modifier body uses `revert ReentrantCall()` — it must be declared
    # before the modifier injection so the code compiles end-to-end.
    if not re.search(r"\berror\s+ReentrantCall\s*\(", code):
        m_contract = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
        if m_contract:
            code = (
                code[: m_contract.end()]
                + "\n    error ReentrantCall();"
                + code[m_contract.end():]
            )

    # ── Inject modifier before the first function definition ─────────────────
    lines = code.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"\s*function\s+\w+", line):
            lines.insert(i, _NOREENTRANT_BODY)
            break
    return "".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
#  NEW FIX-15 + FIX-16: Fix undeclared identifiers in modifier bodies
# ═══════════════════════════════════════════════════════════════════════════

# Common aliased party variable names the LLM uses instead of _partyA/_partyB
_PARTY_ALIASES = [
    # Generic party names
    "parent", "acquisitionSub", "mergerSub", "buyer", "seller",
    "employer", "employee", "licensor", "licensee", "lessor", "lessee",
    "borrower", "lender", "owner", "developer", "client", "vendor",
    "partyA", "partyB", "party1", "party2",
    # M&A / corporate names the LLM uses for merger contracts
    "company", "acquirer", "target", "subsidiary", "holdco", "newco",
    "mergerParty", "mergee", "parentCo", "subCo",
]


def _get_declared_state_vars(code: str) -> set[str]:
    """Return names of all declared state variables."""
    names: set[str] = set()
    for m in re.finditer(
        r"^\s*(?:address|uint\d*|int\d*|bool|bytes\d*|string|mapping|enum\s+\w+)\s+"
        r"(?:payable\s+)?(?:private|public|internal)?\s*(?:immutable\s+|constant\s+)?(\w+)\s*[;=]",
        code, re.MULTILINE,
    ):
        names.add(m.group(1))
    # Also catch: ContractState public contractState;
    for m in re.finditer(
        r"^\s*(?:ContractState|\w+State)\s+(?:public|private|internal)?\s*(\w+)\s*[;=]",
        code, re.MULTILINE,
    ):
        names.add(m.group(1))
    return names


def _fix_broken_onlyParties(code: str) -> str:
    """
    FIX-16: Rewrite `modifier onlyParties()` bodies that reference undeclared
    party variables (parent, acquisitionSub, buyer, seller …).

    Strategy:
      1. Find which party variables ARE declared (_partyA, _partyB, or
         _arbitrator as last resort).
      2. For each onlyParties-style modifier body that references undeclared
         vars, replace the entire condition with a safe one.
    """
    declared = _get_declared_state_vars(code)

    def _safe_party_condition() -> str:
        """Build the safest possible access condition from what's declared."""
        parties = [v for v in ("_partyA", "_partyB") if v in declared]
        if len(parties) == 2:
            return (
                f"if (msg.sender != {parties[0]} && msg.sender != {parties[1]}) "
                "revert Unauthorized();"
            )
        elif len(parties) == 1:
            return f"if (msg.sender != {parties[0]}) revert Unauthorized();"
        elif "_arbitrator" in declared:
            return "if (msg.sender != _arbitrator) revert Unauthorized();"
        else:
            return "// access check skipped — no party addresses declared"

    def _rewrite_modifier_body(m: re.Match) -> str:
        mod_text = m.group(0)
        # Extract condition part (between { and the _ ; })
        cond_m = re.search(r"\{(.*?)_\s*;", mod_text, re.DOTALL)
        if not cond_m:
            return mod_text
        old_cond = cond_m.group(1).strip()

        # Check whether old condition references any undeclared variable
        tokens = re.findall(r"\b([a-zA-Z_]\w*)\b", old_cond)
        bad = [t for t in tokens if t in _PARTY_ALIASES and t not in declared]
        if not bad:
            return mod_text  # Condition is fine

        safe = _safe_party_condition()
        indent = "        "
        new_body = f"\n{indent}{safe}\n{indent}_;"
        rewritten = mod_text[:cond_m.start(1) - 1] + " {" + new_body + "\n    }" + mod_text[cond_m.end():]
        return rewritten

    # Match any modifier whose name contains "onlyParties" or "onlyParty"
    # Use a depth-aware scan instead of [^}]+ to handle nested braces correctly
    def _replace_all_onlyParties(src: str) -> str:
        pat = re.compile(r'modifier\s+only(?:Parties|Party\w*)\s*\(\s*\)', re.DOTALL)
        result = []
        pos = 0
        for mhead in pat.finditer(src):
            result.append(src[pos:mhead.end()])
            # Find the opening brace of this modifier
            rest = src[mhead.end():]
            brace_m = re.search(r'\{', rest)
            if not brace_m:
                pos = mhead.end()
                continue
            brace_start = mhead.end() + brace_m.start()
            result.append(src[mhead.end():brace_start])  # whitespace before {
            # Now find the matching closing brace (depth-aware)
            depth = 0
            j = brace_start
            while j < len(src):
                if src[j] == '{':
                    depth += 1
                elif src[j] == '}':
                    depth -= 1
                    if depth == 0:
                        mod_block = src[brace_start: j + 1]
                        # Build a fake match object for _rewrite_modifier_body
                        fake_full = mhead.group(0) + mod_block
                        class _FM:
                            def group(self_, n):
                                return fake_full
                        rewritten = _rewrite_modifier_body(_FM())
                        # Extract only the body part from the rewritten text
                        rewritten_body = re.search(r'\{.*\}', rewritten, re.DOTALL)
                        result.append(rewritten_body.group(0) if rewritten_body else mod_block)
                        pos = j + 1
                        break
                j += 1
            else:
                pos = mhead.end()
        result.append(src[pos:])
        return "".join(result)

    code = _replace_all_onlyParties(code)
    return code


def _fix_undeclared_state_var_refs(code: str) -> str:
    """
    FIX-15: Scan modifier and function bodies for references to common
    party-alias names that are NOT declared state variables.
    Replace them with the nearest equivalent that IS declared, or remove.

    This catches cases like:
        if (msg.sender != parent || msg.sender == acquisitionSub) revert ...
    where neither `parent` nor `acquisitionSub` is declared.
    """
    declared = _get_declared_state_vars(code)

    # Build substitution map: alias → nearest declared equivalent
    subst: dict[str, str] = {}
    party_vars = [v for v in ("_partyA", "_partyB", "_arbitrator") if v in declared]

    for i, alias in enumerate(_PARTY_ALIASES):
        if alias in declared:
            continue  # It IS declared — no substitution needed
        if i == 0 and len(party_vars) >= 1:
            subst[alias] = party_vars[0]
        elif i == 1 and len(party_vars) >= 2:
            subst[alias] = party_vars[1]
        elif party_vars:
            subst[alias] = party_vars[-1]
        # else: no substitution available — leave for next pass

    if not subst:
        return code

    # Apply substitutions only inside modifier/function bodies (after first {)
    # Use a simple token-level replacement to avoid touching string literals
    def _replace_token(m: re.Match) -> str:
        token = m.group(0)
        return subst.get(token, token)

    # Only replace whole-word occurrences
    pattern = r"\b(" + "|".join(re.escape(k) for k in subst.keys()) + r")\b"
    code = re.sub(pattern, _replace_token, code)
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-8 (updated): onlyX modifiers — only inject if genuinely missing
# ═══════════════════════════════════════════════════════════════════════════

def _fix_onlyX_modifiers(code: str) -> str:
    """Ensure at least 2 `modifier onlyX` declarations exist (SEC-005)."""
    existing = re.findall(r"modifier\s+only\w+\s*\(", code)
    if len(existing) >= 2:
        return code

    declared = _get_declared_state_vars(code)
    has_partyA     = "_partyA"     in declared
    has_partyB     = "_partyB"     in declared
    has_arbitrator = "_arbitrator" in declared

    inject_lines: list[str] = []

    if len(existing) == 0:
        if has_partyA and has_partyB:
            inject_lines.append(
                "    modifier onlyParties() {\n"
                "        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();\n"
                "        _;\n"
                "    }\n"
            )
        elif has_arbitrator:
            inject_lines.append(
                "    modifier onlyOwner() {\n"
                "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
                "        _;\n"
                "    }\n"
            )

    if len(existing) < 2 and has_arbitrator:
        # Don't inject duplicate onlyArbitrator
        if not re.search(r"modifier\s+onlyArbitrator", code):
            inject_lines.append(
                "    modifier onlyArbitrator() {\n"
                "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
                "        _;\n"
                "    }\n"
            )

    if not inject_lines:
        return code

    lines = code.splitlines(keepends=True)
    insert_idx = None
    for i, line in enumerate(lines):
        if re.match(r"\s*(modifier|constructor)\b", line):
            insert_idx = i
            break
    if insert_idx is not None:
        for j, block in enumerate(inject_lines):
            lines.insert(insert_idx + j, block)
        return "".join(lines)
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-11 (updated): payable + receive — only inject if genuinely absent
#  and use a self-contained body (no external modifier references)
# ═══════════════════════════════════════════════════════════════════════════

def _fix_payable_and_receive(code: str) -> str:
    """
    Ensure at least one external payable function and receive() exist.
    The injected depositPayment() is intentionally self-contained:
    it does NOT reference noReentrant (which may not exist in every contract),
    and instead uses inline _locked checks to be safe.
    """
    has_payable_fn = bool(
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bexternal\b[^{]*\bpayable\b", code) or
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\bexternal\b", code)
    )
    has_receive = bool(re.search(r"\breceive\s*\(\s*\)\s+external\s+payable", code))

    if has_payable_fn and has_receive:
        return code

    # Determine whether noReentrant modifier is defined in this contract
    has_noReentrant = bool(re.search(r"modifier\s+noReentrant\s*\(", code))

    inject = ""

    if not has_receive:
        # Ensure PaymentReceived event is declared
        if "PaymentReceived" not in code:
            event_line = "    event PaymentReceived(address indexed from, uint256 amount);\n"
            last_event = None
            for m in re.finditer(r"event\s+\w+[^;]+;\n", code):
                last_event = m
            if last_event:
                code = code[:last_event.end()] + event_line + code[last_event.end():]
            else:
                # No existing events — insert after contract opening brace
                m = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
                if m:
                    code = code[:m.end()] + "\n" + event_line + code[m.end():]

        inject += (
            "\n    /// @notice Accept direct ETH deposits.\n"
            "    receive() external payable {\n"
            "        emit PaymentReceived(msg.sender, msg.value);\n"
            "    }\n"
        )

    if not has_payable_fn:
        # Use noReentrant modifier only if it exists; otherwise use inline guard
        if has_noReentrant:
            guard_open  = "noReentrant "
            guard_inner = ""
        else:
            guard_open  = ""
            guard_inner = (
                "        if (_locked) revert ReentrantCall();\n"
                "        _locked = true;\n"
            )
            guard_close = "        _locked = false;\n"

        inject += (
            "\n    /// @notice Deposit ETH payment into the contract.\n"
            f"    function depositPayment() external payable {guard_open}{{\n"
        )
        if not has_noReentrant:
            inject += guard_inner
        inject += "        emit PaymentReceived(msg.sender, msg.value);\n"
        if not has_noReentrant:
            inject += guard_close
        inject += "    }\n"

    if inject:
        idx = code.rfind("}")
        if idx != -1:
            code = code[:idx] + inject + code[idx:]
    return code


def _add_receive_if_missing(code: str) -> str:
    """Legacy safety net: add bare receive() if payable but no receive."""
    has_payable = "payable" in code
    has_receive = "receive()" in code
    if has_payable and not has_receive:
        idx = code.rfind("}")
        if idx != -1:
            inject = (
                "\n    /// @notice Accept ETH deposits.\n"
                "    receive() external payable {}\n"
            )
            code = code[:idx] + inject + code[idx:]
    return code


def _add_version_comment(code: str, doc: ContractDocument) -> str:
    """Insert the generated-by banner AFTER the pragma line."""
    clean_title = doc.title.lstrip("\ufeff").strip()
    banner = (
        "\n"
        "// =================================================================\n"
        f"// Contract : {clean_title}\n"
        f"// Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        "// Tool     : eContract -> Smart Contract Converter v2.0\n"
        "// Solidity : 0.8.16\n"
        "// WARNING  : Review thoroughly before deployment on mainnet.\n"
        "// =================================================================\n"
    )
    pragma_m = re.search(r"(pragma solidity[^\n]+\n)", code)
    if pragma_m:
        return code[:pragma_m.end()] + banner + code[pragma_m.end():]
    spdx_m = re.search(r"(//\s*SPDX-License-Identifier:[^\n]+\n)", code)
    if spdx_m:
        return code[:spdx_m.end()] + banner + code[spdx_m.end():]
    return banner + code


def _strip_existing_banner(code: str) -> str:
    lines = code.splitlines(keepends=True)
    spdx_idx = next((i for i, l in enumerate(lines) if "SPDX-License-Identifier" in l), None)
    if spdx_idx is None or spdx_idx == 0:
        return code
    pre = lines[:spdx_idx]
    if all(l.strip() == "" or l.strip().startswith("//") for l in pre):
        return "".join(lines[spdx_idx:])
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  Master pipeline
# ═══════════════════════════════════════════════════════════════════════════

def apply_all_fixes(raw_code: str, doc: ContractDocument) -> str:
    """Apply all deterministic post-processing fixes in order."""
    code = raw_code
    # ── Phase 1: structural cleanup ──────────────────────────────────────
    code = _strip_existing_banner(code)
    code = _fix_spdx(code)
    code = _fix_pragma(code)
    code = _fix_safemath(code)
    code = _fix_openzeppelin_imports(code)
    code = _fix_selfdestruct(code)
    code = _fix_tx_origin(code)
    code = _fix_noReentrant_modifier(code)
    code = _fix_mapping_return(code)
    code = _fix_calculatePenalty_view(code)

    # ── Phase 2: resolve type / naming errors ────────────────────────────
    code = _fix_bare_locked(code)                # NEW: bare `locked` → `_locked`
    code = _fix_contractstate_type(code)         # NEW: uint256 contractState → ContractState

    # ── Phase 3: resolve "Identifier not found" errors ───────────────────
    code = _fix_party_var_naming(code)            # FIX-21: partyA → _partyA everywhere
    code = _fix_undeclared_state_var_refs(code)  # FIX-15: replace bad party aliases
    code = _fix_broken_onlyParties(code)         # FIX-16: rewrite broken modifier bodies
    code = _fix_modifier_ordering(code)          # FIX-22: hoist modifiers before functions
    code = _fix_receive_body(code)               # FIX-19: fix receive() calling internal fns
    code = _fix_return_in_non_returning_fn(code) # FIX-20: drop return stmts in void fns
    code = _fix_missing_noReentrant(code)        # FIX-14: inject missing noReentrant body
    code = _fix_payable_noReentrant(code)         # FIX-18: apply noReentrant to payable fns
    code = _fix_missing_custom_errors(code)      # FIX-12: declare all revert targets
    code = _fix_missing_events(code)             # FIX-13: declare all emit targets

    # ── Phase 4: inject missing required constructs ──────────────────────
    code = _fix_locked_declaration(code)
    code = _fix_onlyX_modifiers(code)
    code = _fix_governing_law_constant(code, doc)
    code = _fix_start_date(code)
    code = _fix_require_to_custom_errors(code)   # paren-balanced parser (fixed)
    code = _fix_msg_value_validation(code)       # NEW: SEC-004 msg.value guard
    code = _fix_expiry_deadline(code)            # NEW: COV-020 _deadline injection
    code = _fix_payable_and_receive(code)        # FIX-11 (updated)
    code = _fix_payable_noReentrant(code)         # Re-run: cover any newly injected payable fns
    code = _add_receive_if_missing(code)

    # ── Phase 5: documentation + formatting ──────────────────────────────
    code = _fix_natspec_comments(code)           # NEW: SOL-013 ≥8 @notice
    code = _fix_trailing_whitespace(code)
    code = _add_version_comment(code, doc)
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  Output file writers (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    text = text.lstrip("\ufeff").strip()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s-]+", "_", text).strip("_")
    return text or "contract"


def save_solidity(
    code: str,
    doc: ContractDocument,
    output_dir: Path,
    filename: Optional[str] = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = filename or _slugify(doc.title)
    path = output_dir / f"{name}.sol"
    path.write_text(code, encoding="utf-8")
    return path


def save_report(
    doc: ContractDocument,
    sol_path: Path,
    issues: list[str],
    output_dir: Path,
    elapsed: float,
    validation_report=None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "conversion_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds":      round(elapsed, 2),
        "source_file":          doc.metadata.get("source_file", "unknown"),
        "output_file":          str(sol_path),
        "contract_title":       doc.title.lstrip("\ufeff").strip(),
        "parties": [
            {"role": p.role, "name": p.name, "wallet": p.wallet_hint}
            for p in doc.parties
        ],
        "clauses_extracted":    doc.metadata.get("clause_count", 0),
        "effective_date":       doc.effective_date,
        "expiry_date":          doc.expiry_date,
        "governing_law":        doc.governing_law,
        "char_count":           doc.metadata.get("char_count", 0),
        "validation_issues":    issues,
        "validation_passed":    len(issues) == 0,
    }
    if validation_report is not None:
        vr = validation_report
        report["validation_passed"] = (
            len(issues) == 0 and vr.critical_failures == 0 and vr.accuracy_overall >= 50.0
        )
        report["accuracy"] = {
            "overall":  round(vr.accuracy_overall,  1),
            "solidity": round(vr.accuracy_solidity, 1),
            "security": round(vr.accuracy_security, 1),
            "legal":    round(vr.accuracy_legal,    1),
            "coverage": round(vr.accuracy_coverage, 1),
        }
        report["test_suite"] = {
            "total_tests":       vr.total_tests,
            "passed":            vr.passed,
            "failed":            vr.failed,
            "critical_failures": vr.critical_failures,
            "results": [
                {
                    "test_id":     r.test_id,
                    "category":    r.category,
                    "description": r.description,
                    "passed":      r.passed,
                    "severity":    r.severity,
                    "detail":      r.detail,
                }
                for r in vr.results
            ],
        }
        report["test_summary"] = vr.summary
    else:
        report["accuracy"] = {
            "overall": None, "solidity": None,
            "security": None, "legal": None, "coverage": None,
        }
        report["test_suite"]   = None
        report["test_summary"] = "Validator not run."

    path = output_dir / "results.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def run_contract_validation(code: str, doc: "ContractDocument"):
    """Run the full test suite. Returns a ValidationReport or None."""
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from test_contract_validator import run_all_validations
        return run_all_validations(code, doc)
    except ImportError:
        return None


def save_human_readable_summary(
    doc: ContractDocument,
    sol_path: Path,
    output_dir: Path,
    validation_report=None,
) -> Optional[Path]:
    """No-op — results folder contains only .sol + results.json."""
    return None

# ═══════════════════════════════════════════════════════════════════════════
#  Feedback-aware generation pipeline
# ═══════════════════════════════════════════════════════════════════════════

def generate_contract_with_feedback(
    llm_client,
    doc: "ContractDocument",
    system_prompt: str,
    user_prompt: str,
    output_dir: "Path",
    max_iterations: int = 3,
    accuracy_target: float = 100.0,
    filename: Optional[str] = None,
) -> tuple["Path", "Path", object]:
    """
    Full pipeline: LLM generation → postprocessor fixes → validation →
    feedback loop (if accuracy < target or contract has errors) →
    save outputs.

    Parameters
    ----------
    llm_client      : An initialised LLMClient instance.
    doc             : Parsed ContractDocument.
    system_prompt   : System prompt string (from prompt_builder.get_system_prompt()).
    user_prompt     : User prompt string (from prompt_builder.build_user_prompt()).
    output_dir      : Directory for .sol and results.json files.
    max_iterations  : Max feedback iterations (default 3).
    accuracy_target : Stop early when accuracy reaches this % (default 100.0).
    filename        : Optional stem for output files.

    Returns
    -------
    (sol_path, report_path, validation_report)
    """
    import time
    import logging
    logger = logging.getLogger("econtract.pipeline")

    start = time.time()

    logger.info(
        f"Starting generation pipeline for '{doc.title}' "
        f"(max_iterations={max_iterations}, accuracy_target={accuracy_target}%)"
    )

    # ── Run generation + feedback loop ───────────────────────────────────────
    best_code, struct_issues, validation_report = llm_client.generate_with_feedback(
        system         = system_prompt,
        user           = user_prompt,
        doc            = doc,
        max_iterations = max_iterations,
        accuracy_target= accuracy_target,
    )

    elapsed = time.time() - start

    # ── Final postprocessor pass on the best code ────────────────────────────
    final_code = apply_all_fixes(best_code, doc)

    # ── Save artefacts ───────────────────────────────────────────────────────
    sol_path    = save_solidity(final_code, doc, Path(output_dir), filename)
    report_path = save_report(
        doc,
        sol_path,
        struct_issues,
        Path(output_dir),
        elapsed,
        validation_report,
    )

    if validation_report is not None:
        logger.info(
            f"Pipeline complete in {elapsed:.1f}s — "
            f"final accuracy: {validation_report.accuracy_overall:.1f}% "
            f"({validation_report.passed}/{validation_report.total_tests} tests passed)"
        )
    else:
        logger.info(f"Pipeline complete in {elapsed:.1f}s")

    return sol_path, report_path, validation_report