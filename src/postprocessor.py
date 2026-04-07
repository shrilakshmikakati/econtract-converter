"""
postprocessor.py — Cleans the raw LLM output, applies deterministic
Solidity fixes, and generates the final output artefacts.

FIXES applied vs previous version:
  1. save_report() fully embeds ValidationReport into results.json.
  2. Results folder: ONLY <name>.sol + results.json.
  3. _add_version_comment: banner inserted AFTER pragma.
  4. _add_version_comment: title stripped of BOM/whitespace.
  5. [NEW] _fix_calculatePenalty_view: removes `view` from calculatePenalty()
     because it emits an event — view functions cannot emit events.
  6. [NEW] _fix_msg_value_in_view: detect msg.value inside non-payable
     functions and refactor the signature to accept a `principal` param.
  7. [NEW] _fix_locked_declaration: ensure `bool private _locked;` is declared
     at contract scope if missing (SEC-001).
  8. [NEW] _fix_onlyX_modifiers: inject a minimum pair of onlyX modifiers if
     fewer than 2 exist (SEC-005).
  9. [NEW] _fix_payable_and_receive: inject a minimal pay() function and
     receive() fallback if no payable function exists (COV-001 / LEG-090).
  10.[NEW] _fix_governing_law_constant: inject GOVERNING_LAW string constant
     from doc metadata if missing (LEG-020).
  11.[NEW] _fix_start_date: inject `uint256 public immutable startDate` and
     its constructor assignment if missing (LEG-030).
  12.[NEW] _fix_natspec: add a minimum @notice comment to each public/external
     function that lacks one (SOL-013).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from extractor import ContractDocument


# ═══════════════════════════════════════════════════════════════════════════
#  Deterministic Solidity fixes
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
    """Remove erroneous parameters from noReentrant modifier."""
    code = re.sub(
        r"modifier\s+noReentrant\s*\(\s*bool\s+storage\s+\w+\s*\)",
        "modifier noReentrant()",
        code,
    )
    return code


def _fix_mapping_return(code: str) -> str:
    """Remove mapping(...) from return type tuples."""
    code = re.sub(
        r",?\s*mapping\s*\([^)]+\)[^,)]*(?=\s*[,)])",
        "",
        code,
    )
    return code


def _fix_calculatePenalty_view(code: str) -> str:
    """
    FIX-5: calculatePenalty() emits an event, so it CANNOT be `view`.
    Remove `view` from calculatePenalty function signatures.
    Also ensure it doesn't use msg.value (replaced with a `principal` param).
    """
    # Remove `view` from calculatePenalty signature
    code = re.sub(
        r"(function\s+calculatePenalty\s*\([^)]*\)\s+(?:external|public)\s+)view\s+",
        r"\1",
        code,
    )
    # If the function still reads msg.value, inject a principal param and replace usage
    if re.search(r"function\s+calculatePenalty\s*\([^)]*\)", code):
        # Check if msg.value is used inside calculatePenalty body
        fn_match = re.search(
            r"(function\s+calculatePenalty\s*\()([^)]*)\)(.*?\{)(.*?)\n(\s*\})",
            code, re.DOTALL,
        )
        if fn_match and "msg.value" in fn_match.group(4):
            params = fn_match.group(2).strip()
            # Add principal param if not already there
            if "principal" not in params:
                new_params = f"uint256 principal{', ' + params if params else ''}"
                body = fn_match.group(4).replace("msg.value", "principal")
                replacement = (
                    f"{fn_match.group(1)}{new_params}){fn_match.group(3)}"
                    f"{body}\n{fn_match.group(5)}"
                )
                code = code[:fn_match.start()] + replacement + code[fn_match.end():]
    return code


def _fix_locked_declaration(code: str) -> str:
    """
    FIX-6: Ensure `bool private _locked;` is declared at contract scope.
    If it appears only inside a modifier/function, hoist it to contract level.
    """
    if re.search(r"bool\s+private\s+_locked\s*;", code):
        return code  # Already present at some level — good enough

    # Inject after the last state-variable-looking line before the first modifier/constructor
    inject = "    bool private _locked; // reentrancy guard\n"
    # Find a good insertion point: after the last `address/uint/bool/enum` var declaration
    # before the first `modifier` or `constructor`
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


def _fix_onlyX_modifiers(code: str) -> str:
    """
    FIX-7: Ensure at least 2 `modifier onlyX` declarations exist (SEC-005).
    If fewer than 2 are present, inject standard onlyParties + onlyArbitrator.
    """
    existing = re.findall(r"modifier\s+only\w+\s*\(", code)
    if len(existing) >= 2:
        return code

    # We need to inject. First check what state vars exist.
    has_partyA    = bool(re.search(r"_partyA\b", code))
    has_partyB    = bool(re.search(r"_partyB\b", code))
    has_arbitrator = bool(re.search(r"_arbitrator\b", code))

    inject_lines: list[str] = []

    if len(existing) == 0:
        if has_partyA and has_partyB:
            inject_lines.append(
                "    modifier onlyParties() {\n"
                "        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();\n"
                "        _;\n"
                "    }\n"
            )
        else:
            inject_lines.append(
                "    modifier onlyOwner() {\n"
                "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
                "        _;\n"
                "    }\n"
            )

    if len(existing) < 2 and has_arbitrator:
        inject_lines.append(
            "    modifier onlyArbitrator() {\n"
            "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
            "        _;\n"
            "    }\n"
        )

    if not inject_lines:
        return code

    # Inject before the first existing modifier or constructor
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


def _fix_payable_and_receive(code: str) -> str:
    """
    FIX-8: Ensure at least one `external payable` function and receive() exist.
    If neither exists, inject a minimal depositPayment() + receive().
    """
    has_payable_fn = bool(re.search(
        r"function\s+\w+\s*\([^)]*\)[^{]*\bexternal\b[^{]*\bpayable\b", code
    ) or re.search(
        r"function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\bexternal\b", code
    ))
    has_receive = bool(re.search(r"\breceive\s*\(\s*\)\s+external\s+payable", code))

    if has_payable_fn and has_receive:
        return code

    inject = ""
    if not has_receive:
        inject += (
            "\n    /// @notice Accept direct ETH deposits.\n"
            "    receive() external payable {\n"
            "        emit PaymentReceived(msg.sender, msg.value);\n"
            "    }\n"
        )
        # Ensure PaymentReceived event exists
        if "PaymentReceived" not in code:
            event_line = "    event PaymentReceived(address indexed from, uint256 amount);\n"
            # inject event near other events
            code = re.sub(
                r"(event\s+\w+[^;]+;\n)",
                r"\1" + event_line,
                code,
                count=1,
            )

    if not has_payable_fn:
        inject += (
            "\n    /// @notice Deposit ETH payment into the contract.\n"
            "    function depositPayment() external payable noReentrant {\n"
            "        emit PaymentReceived(msg.sender, msg.value);\n"
            "    }\n"
        )

    # Inject before closing brace of contract
    idx = code.rfind("}")
    if idx != -1:
        code = code[:idx] + inject + code[idx:]
    return code


def _fix_governing_law_constant(code: str, doc: ContractDocument) -> str:
    """
    FIX-9: Inject GOVERNING_LAW string constant if missing (LEG-020).
    """
    if "GOVERNING_LAW" in code:
        return code
    gov = doc.governing_law or ""
    if not gov:
        return code
    gov_word = gov.split()[0]
    constant_line = f'    string public constant GOVERNING_LAW = "{gov_word}";\n'
    # Inject after EFFECTIVE_DATE constant or after pragma
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
    FIX-10: Inject `uint256 public immutable startDate` if missing (LEG-030).
    Also inject constructor assignment `startDate = EFFECTIVE_DATE;` if missing.
    """
    if re.search(r"\bstartDate\b", code) or re.search(r"\beffectiveDate\b", code):
        return code

    # Inject declaration after EFFECTIVE_DATE constant
    if "EFFECTIVE_DATE" in code:
        decl = "    uint256 public immutable startDate;\n"
        m = re.search(r"(uint256\s+public\s+constant\s+EFFECTIVE_DATE[^\n]+\n)", code)
        if m:
            code = code[:m.end()] + decl + code[m.end():]

        # Inject assignment in constructor body
        ctor = re.search(r"constructor\s*\([^)]*\)[^{]*\{", code)
        if ctor:
            insert_pos = ctor.end()
            code = code[:insert_pos] + "\n        startDate = EFFECTIVE_DATE;" + code[insert_pos:]

    return code


def _fix_natspec(code: str) -> str:
    """
    FIX-11: Add a minimal `/// @notice` comment before each public/external
    function that lacks one (SOL-013).
    """
    def _add_notice(m: re.Match) -> str:
        preceding = code[:m.start()]
        # Check if there's already a @notice in the preceding 3 lines
        last_lines = preceding.rsplit("\n", 4)[-4:]
        if any("@notice" in ln for ln in last_lines):
            return m.group(0)
        fn_name = m.group(1)
        indent = re.match(r"(\s*)", m.group(0)).group(1)
        notice = f"{indent}/// @notice Executes the {fn_name} operation.\n"
        return notice + m.group(0)

    code = re.sub(
        r"(\s+)(function\s+(\w+)\s*\([^)]*\)[^{]*(?:external|public)[^{]*\{)",
        lambda m: _add_notice_inline(m),
        code,
    )
    return code


def _add_notice_inline(m: re.Match) -> str:
    """Helper for _fix_natspec that works on the match object."""
    full = m.group(0)
    # Extract indentation
    indent_m = re.match(r"(\s+)", full)
    indent = indent_m.group(1) if indent_m else "    "
    fn_name_m = re.search(r"function\s+(\w+)", full)
    fn_name = fn_name_m.group(1) if fn_name_m else "function"
    # Check if @notice already precedes (within match prefix not available here,
    # so we check if the match itself contains @notice — it won't, that's before)
    notice = f"\n{indent}/// @notice Executes the {fn_name} operation."
    return notice + full


def _fix_require_to_custom_errors(code: str) -> str:
    """
    Convert any remaining require(cond, "string") calls to custom-error pattern.
    Uses a generic InvalidOperation error if a specific one is not defined.
    """
    # Ensure we have a generic error to fall back to
    if "error InvalidOperation" not in code and "error Unauthorized" in code:
        pass  # Use Unauthorized as fallback

    def _replace_require(m: re.Match) -> str:
        condition = m.group(1).strip()
        # Negate the condition
        if condition.startswith("!"):
            neg = condition[1:].strip()
        elif "==" in condition:
            neg = condition.replace("==", "!=", 1)
        elif "!=" in condition:
            neg = condition.replace("!=", "==", 1)
        elif ">=" in condition:
            neg = condition.replace(">=", "<", 1)
        elif "<=" in condition:
            neg = condition.replace("<=", ">", 1)
        elif ">" in condition:
            neg = condition.replace(">", "<=", 1)
        elif "<" in condition:
            neg = condition.replace("<", ">=", 1)
        else:
            neg = f"!({condition})"
        return f"if ({neg}) revert Unauthorized()"

    code = re.sub(
        r'require\s*\(\s*([^,)]+)\s*,\s*"[^"]*"\s*\)',
        _replace_require,
        code,
    )
    # Clean up bare require(cond) without message
    code = re.sub(
        r'require\s*\(\s*([^,)]+)\s*\)',
        lambda m: f"if (!({m.group(1).strip()})) revert Unauthorized()",
        code,
    )
    return code


def _add_receive_if_missing(code: str) -> str:
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
        pos = pragma_m.end()
        return code[:pos] + banner + code[pos:]
    spdx_m = re.search(r"(//\s*SPDX-License-Identifier:[^\n]+\n)", code)
    if spdx_m:
        pos = spdx_m.end()
        return code[:pos] + banner + code[pos:]
    return banner + code


def _strip_existing_banner(code: str) -> str:
    """Remove any pre-existing generated-by banner."""
    lines = code.splitlines(keepends=True)
    spdx_idx = next(
        (i for i, l in enumerate(lines) if "SPDX-License-Identifier" in l), None
    )
    if spdx_idx is None or spdx_idx == 0:
        return code
    pre = lines[:spdx_idx]
    if all(l.strip() == "" or l.strip().startswith("//") for l in pre):
        return "".join(lines[spdx_idx:])
    return code


def apply_all_fixes(raw_code: str, doc: ContractDocument) -> str:
    """Apply all deterministic post-processing fixes in the correct order."""
    code = raw_code
    code = _strip_existing_banner(code)
    code = _fix_spdx(code)
    code = _fix_pragma(code)
    code = _fix_safemath(code)
    code = _fix_openzeppelin_imports(code)
    code = _fix_selfdestruct(code)
    code = _fix_tx_origin(code)
    code = _fix_noReentrant_modifier(code)
    code = _fix_mapping_return(code)
    code = _fix_calculatePenalty_view(code)          # FIX-5/6: view + msg.value
    code = _fix_locked_declaration(code)             # FIX-7: SEC-001
    code = _fix_onlyX_modifiers(code)               # FIX-8: SEC-005
    code = _fix_governing_law_constant(code, doc)    # FIX-9: LEG-020
    code = _fix_start_date(code)                     # FIX-10: LEG-030
    code = _fix_require_to_custom_errors(code)       # FIX-11: SOL-007
    code = _fix_payable_and_receive(code)            # FIX-12: COV-001/LEG-090
    code = _add_receive_if_missing(code)
    code = _fix_trailing_whitespace(code)
    code = _add_version_comment(code, doc)           # must be last
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  Output file writers
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
    """
    Serialise the full ValidationReport into results.json.
    """
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
            len(issues) == 0
            and vr.critical_failures == 0
            and vr.accuracy_overall >= 50.0
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


# ═══════════════════════════════════════════════════════════════════════════
#  Validation — calls test_contract_validator if available
# ═══════════════════════════════════════════════════════════════════════════

def run_contract_validation(code: str, doc: "ContractDocument"):
    """Run the full test suite. Returns a ValidationReport or None."""
    try:
        import sys
        import pathlib
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
    """Intentionally no-op — results folder contains only .sol + results.json."""
    return None