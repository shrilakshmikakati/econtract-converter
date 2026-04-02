"""
postprocessor.py — Cleans the raw LLM output, applies deterministic
Solidity fixes, and generates the final output artefacts.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from extractor import ContractDocument


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity fixes applied deterministically (no LLM needed)
# ═══════════════════════════════════════════════════════════════════════════

def _fix_pragma(code: str) -> str:
    """Ensure the pragma is exactly 0.8.16."""
    # Replace any 0.8.x pragma
    code = re.sub(
        r"pragma\s+solidity\s+[\^~]?0\.\d+\.\d+;",
        "pragma solidity ^0.8.16;",
        code,
    )
    # If no pragma at all, inject one after SPDX
    if "pragma solidity" not in code:
        code = re.sub(
            r"(//\s*SPDX-License-Identifier:[^\n]+\n)",
            r"\1pragma solidity ^0.8.16;\n",
            code,
        )
    return code


def _fix_spdx(code: str) -> str:
    """Ensure SPDX header is present as the first non-blank line."""
    if "SPDX-License-Identifier" not in code:
        code = "// SPDX-License-Identifier: MIT\n" + code
    return code


def _fix_trailing_whitespace(code: str) -> str:
    lines = [ln.rstrip() for ln in code.splitlines()]
    return "\n".join(lines).strip() + "\n"


def _fix_safemath(code: str) -> str:
    """Remove SafeMath imports/usage — not needed in 0.8.x."""
    # Remove import statements for SafeMath
    code = re.sub(r'import\s+["\'].*[Ss]afe[Mm]ath.*["\'];\n?', "", code)
    # Remove `using SafeMath for ...;`
    code = re.sub(r"using\s+SafeMath\s+for\s+[^;]+;\n?", "", code)
    return code


def _fix_openzeppelin_imports(code: str) -> str:
    """
    Remove OpenZeppelin imports and replace with inline equivalents.
    We generate standalone contracts.
    """
    # Remove all @openzeppelin imports
    code = re.sub(r'import\s+["\']@openzeppelin/[^"\']+["\'];\n?', "", code)
    # Remove `is Ownable` etc. — keep contract body intact
    code = re.sub(r"\bis\s+(?:Ownable|ReentrancyGuard|Pausable)\b", "", code)
    return code


def _fix_selfdestruct(code: str) -> str:
    """Replace selfdestruct with a comment."""
    return re.sub(
        r"selfdestruct\s*\([^)]*\)\s*;",
        "// selfdestruct removed — deprecated in Solidity 0.8.x",
        code,
    )


def _fix_tx_origin(code: str) -> str:
    """Replace tx.origin auth with msg.sender."""
    return re.sub(r"\btx\.origin\b", "msg.sender /* was tx.origin — fixed */", code)


def _add_receive_if_missing(code: str) -> str:
    """If contract has payable functions but no receive(), add one."""
    has_payable = "payable" in code
    has_receive = "receive()" in code
    if has_payable and not has_receive:
        # Insert before the last `}` of the contract
        idx = code.rfind("}")
        if idx != -1:
            inject = (
                "\n    /// @notice Accept ETH deposits.\n"
                "    receive() external payable {}\n"
            )
            code = code[:idx] + inject + code[idx:]
    return code


def _add_version_comment(code: str, doc: ContractDocument) -> str:
    """Prepend a generated-by banner."""
    banner = (
        f"// ═══════════════════════════════════════════════════════════════\n"
        f"// Contract : {doc.title}\n"
        f"// Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"// Tool     : eContract → Smart Contract Converter v1.0\n"
        f"// Solidity : 0.8.16\n"
        f"// WARNING  : Review thoroughly before deployment on mainnet.\n"
        f"// ═══════════════════════════════════════════════════════════════\n"
    )
    return banner + code


def apply_all_fixes(raw_code: str, doc: ContractDocument) -> str:
    """Apply all deterministic post-processing fixes."""
    code = raw_code
    code = _fix_spdx(code)
    code = _fix_pragma(code)
    code = _fix_safemath(code)
    code = _fix_openzeppelin_imports(code)
    code = _fix_selfdestruct(code)
    code = _fix_tx_origin(code)
    code = _add_receive_if_missing(code)
    code = _fix_trailing_whitespace(code)
    code = _add_version_comment(code, doc)
    return code


# ═══════════════════════════════════════════════════════════════════════════
#  Output file writers
# ═══════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    """Convert title to snake_case filename."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s-]+", "_", text).strip("_")
    return text or "contract"


def save_solidity(
    code: str,
    doc: ContractDocument,
    output_dir: Path,
    filename: Optional[str] = None,
) -> Path:
    """Save the .sol file."""
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
) -> Path:
    """Save a JSON conversion report."""
    report = {
        "conversion_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
        "source_file": doc.metadata.get("source_file", "unknown"),
        "output_file": str(sol_path),
        "contract_title": doc.title,
        "parties": [
            {"role": p.role, "name": p.name, "wallet": p.wallet_hint}
            for p in doc.parties
        ],
        "clauses_extracted": doc.metadata.get("clause_count", 0),
        "effective_date": doc.effective_date,
        "expiry_date": doc.expiry_date,
        "governing_law": doc.governing_law,
        "validation_issues": issues,
        "validation_passed": len(issues) == 0,
        "char_count": doc.metadata.get("char_count", 0),
    }
    name = _slugify(doc.title) + "_report"
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def save_human_readable_summary(
    doc: ContractDocument,
    sol_path: Path,
    output_dir: Path,
) -> Path:
    """Save a human-readable .md summary of the conversion."""
    lines = [
        f"# Smart Contract Summary: {doc.title}",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}  ",
        f"**Solidity Version:** 0.8.16  ",
        f"**Output File:** `{sol_path.name}`  ",
        "",
        "## Parties",
        "",
    ]
    for p in doc.parties:
        w = f" — ETH: `{p.wallet_hint}`" if p.wallet_hint else ""
        lines.append(f"- **{p.role}:** {p.name}{w}")

    lines += [
        "",
        "## Contract Terms",
        "",
        f"- **Effective Date:** {doc.effective_date or 'N/A'}",
        f"- **Expiry Date:** {doc.expiry_date or 'N/A'}",
        f"- **Governing Law:** {doc.governing_law or 'N/A'}",
        "",
        "## Clauses Converted",
        "",
    ]
    for c in doc.clauses:
        lines.append(f"### {c.index+1}. {c.heading} `[{c.clause_type}]`")
        snippet = c.raw_text[:300].replace("\n", " ")
        if len(c.raw_text) > 300:
            snippet += "..."
        lines.append(f"> {snippet}")
        if c.amount_eth:
            lines.append(f"- **Amount:** {c.amount_eth}")
        if c.deadline_days:
            lines.append(f"- **Deadline:** {c.deadline_days} days")
        lines.append("")

    lines += [
        "## ⚠️ Disclaimer",
        "",
        "This contract was auto-generated from an eContract document. "
        "**Always have a qualified Solidity auditor review before mainnet deployment.**",
        "",
    ]

    name = _slugify(doc.title) + "_summary"
    path = output_dir / f"{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
