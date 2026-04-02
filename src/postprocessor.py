"""
postprocessor.py — Cleans the raw LLM output, applies deterministic
Solidity fixes, and generates the final output artefacts.

FIXES applied vs original:
  1. _add_version_comment: banner now inserted AFTER pragma line so
     SPDX-License-Identifier stays as the very first line (solc requirement).
  2. _add_version_comment: title is stripped of BOM/whitespace before use.
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
    FIX 1 + 2: Insert the generated-by banner AFTER the pragma line so the
    file order is:
        // SPDX-License-Identifier: MIT   ← line 1  (solc requires this first)
        pragma solidity ^0.8.16;          ← line 2
        // ═══ banner ═══                 ← lines 3-9
        contract ...

    Also strips BOM / leading whitespace from the title string.
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
    # Insert after pragma line
    pragma_m = re.search(r"(pragma solidity[^\n]+\n)", code)
    if pragma_m:
        pos = pragma_m.end()
        return code[:pos] + banner + code[pos:]
    # Fallback: insert after SPDX line
    spdx_m = re.search(r"(//\s*SPDX-License-Identifier:[^\n]+\n)", code)
    if spdx_m:
        pos = spdx_m.end()
        return code[:pos] + banner + code[pos:]
    # Last resort: prepend
    return banner + code


def _strip_existing_banner(code: str) -> str:
    """
    Remove any pre-existing generated-by banner block (lines starting with
    // ═══ or // === before the SPDX line) so that re-processing a file
    doesn't produce a double banner.
    """
    lines = code.splitlines(keepends=True)
    # Find where SPDX line is
    spdx_idx = next(
        (i for i, l in enumerate(lines) if "SPDX-License-Identifier" in l), None
    )
    if spdx_idx is None or spdx_idx == 0:
        return code
    # Drop everything before the SPDX line if it's all comment/blank lines
    pre = lines[:spdx_idx]
    if all(l.strip() == "" or l.strip().startswith("//") for l in pre):
        return "".join(lines[spdx_idx:])
    return code


def apply_all_fixes(raw_code: str, doc: ContractDocument) -> str:
    """Apply all deterministic post-processing fixes in the correct order."""
    code = raw_code
    code = _strip_existing_banner(code)   # remove stale banner if re-processing
    code = _fix_spdx(code)
    code = _fix_pragma(code)
    code = _fix_safemath(code)
    code = _fix_openzeppelin_imports(code)
    code = _fix_selfdestruct(code)
    code = _fix_tx_origin(code)
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
) -> Path:
    report = {
        "conversion_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
        "source_file": doc.metadata.get("source_file", "unknown"),
        "output_file": str(sol_path),
        "contract_title": doc.title.lstrip("\ufeff").strip(),
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
    lines = [
        f"# Smart Contract Summary: {doc.title.lstrip(chr(0xFEFF)).strip()}",
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
        "## Warning",
        "",
        "This contract was auto-generated from an eContract document. "
        "**Always have a qualified Solidity auditor review before mainnet deployment.**",
        "",
    ]

    name = _slugify(doc.title) + "_summary"
    path = output_dir / f"{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path