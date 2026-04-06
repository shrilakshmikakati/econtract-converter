"""
postprocessor.py — Cleans the raw LLM output, applies deterministic
Solidity fixes, and generates the final output artefacts.

FIXES applied:
  1. save_report() now fully embeds the ValidationReport (accuracy scores,
     per-test results, summary) into results.json — previously it ignored it.
  2. Results folder contains ONLY: <name>.sol + results.json (no .md summary,
     no logs, no temp files).
  3. _add_version_comment: banner inserted AFTER pragma so SPDX stays first.
  4. _add_version_comment: title stripped of BOM/whitespace before use.
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
    """
    FIX: The LLM often generates noReentrant with a bool storage parameter,
    which is invalid Solidity. Replace with the correct parameterless pattern.
    """
    # Fix modifier noReentrant(bool storage _locked) → modifier noReentrant()
    code = re.sub(
        r"modifier\s+noReentrant\s*\(\s*bool\s+storage\s+\w+\s*\)",
        "modifier noReentrant()",
        code,
    )
    return code


def _fix_mapping_return(code: str) -> str:
    """
    FIX: Solidity cannot return a mapping type from a function.
    Remove mapping(...) from return type tuples — the compiler rejects it.
    """
    # Remove mapping(address => uint256) from returns(...) tuples
    code = re.sub(
        r",?\s*mapping\s*\([^)]+\)[^,)]*(?=\s*[,)])",
        "",
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
    """
    Insert the generated-by banner AFTER the pragma line so the
    file order is:
        // SPDX-License-Identifier: MIT   ← line 1  (solc requires this first)
        pragma solidity ^0.8.16;          ← line 2
        // ═══ banner ═══                 ← lines 3-9
        contract ...
    """
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
    """Remove any pre-existing generated-by banner so re-processing doesn't double up."""
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
    code = _fix_noReentrant_modifier(code)   # NEW: fix bad modifier signature
    code = _fix_mapping_return(code)         # NEW: fix un-returnable mapping type
    code = _add_receive_if_missing(code)
    code = _fix_trailing_whitespace(code)
    code = _add_version_comment(code, doc)   # must be last
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
    FIX: validation_report (a ValidationReport dataclass from
    test_contract_validator.py) is now fully serialised into results.json.

    The JSON now contains:
      - All original metadata fields
      - validation_issues     : structural issues from llm_client validator
      - validation_passed     : True only if both structural + test suite pass
      - accuracy              : nested dict with overall + per-category scores
      - test_suite            : full per-test pass/fail list with severity/detail
      - test_summary          : human-readable one-liner
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Base report ──────────────────────────────────────────────────────────
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

        # Structural issues from llm_client.validate_solidity_output()
        "validation_issues":    issues,
        "validation_passed":    len(issues) == 0,
    }

    # ── FIX: embed full ValidationReport if available ───────────────────────
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
        # No test suite available — mark accuracy as unknown
        report["accuracy"] = {
            "overall":  None,
            "solidity": None,
            "security": None,
            "legal":    None,
            "coverage": None,
        }
        report["test_suite"]  = None
        report["test_summary"] = "Validator not run."

    name = _slugify(doc.title) + "_report"
    # ── FIX: save as results.json (not <title>_report.json) ─────────────────
    path = output_dir / "results.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════
#  Validation — calls test_contract_validator if available
# ═══════════════════════════════════════════════════════════════════════════

def run_contract_validation(code: str, doc: "ContractDocument"):
    """
    Run the full test suite (test_contract_validator.py) against the
    generated Solidity code.  Returns a ValidationReport or None.
    """
    try:
        import sys
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from test_contract_validator import run_all_validations
        return run_all_validations(code, doc)
    except ImportError:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Human-readable summary  (DISABLED — results folder is .sol + results.json only)
# ═══════════════════════════════════════════════════════════════════════════

def save_human_readable_summary(
    doc: ContractDocument,
    sol_path: Path,
    output_dir: Path,
    validation_report=None,
) -> Optional[Path]:
    """
    Intentionally returns None — the results folder must contain ONLY:
      • <contract_name>.sol
      • results.json
    The summary markdown is no longer written to disk.
    """
    return None