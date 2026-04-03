#!/usr/bin/env python3
"""
test_contract_validator.py — Smart Contract Legal Compliance Validator

Validates that a generated Solidity smart contract faithfully encodes
every legal obligation, clause, and condition extracted from the source
eContract.

Produces:
  • Detailed pass/fail per test
  • An accuracy score (0–100) split into categories
  • A structured dict ready to be merged into results.json

Usage (standalone):
    python test_contract_validator.py <contract.sol> <source.txt|docx>

Usage (imported by postprocessor / econtract_converter):
    from test_contract_validator import run_all_validations, ValidationReport
"""

from __future__ import annotations

import re
import sys
import json
import argparse
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple, Dict

# ── project imports (resolve when run from project root or tests/) ─────────
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from extractor import extract_contract, ContractDocument, ContractClause

logger = logging.getLogger("econtract.validator")


# ═══════════════════════════════════════════════════════════════════════════
#  Data model
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    test_id:     str
    category:    str          # legal | solidity | security | coverage
    description: str
    passed:      bool
    severity:    str          # critical | major | minor | info
    detail:      str = ""     # what was found / what was missing


@dataclass
class ValidationReport:
    total_tests:      int  = 0
    passed:           int  = 0
    failed:           int  = 0
    critical_failures: int = 0

    accuracy_overall:      float = 0.0
    accuracy_legal:        float = 0.0
    accuracy_solidity:     float = 0.0
    accuracy_security:     float = 0.0
    accuracy_coverage:     float = 0.0

    results: List[TestResult] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [asdict(r) for r in self.results]
        return d


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _eth_to_wei(eth_str: str) -> Optional[int]:
    """Convert a string like '3 ETH' or '10 ETH' to wei (int)."""
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:ETH|ether)", eth_str, re.I)
    if m:
        val = float(m.group(1).replace(",", ""))
        return int(val * 10**18)
    return None


def _find_in_sol(pattern: str, code: str, flags: int = re.I) -> bool:
    return bool(re.search(pattern, code, flags))


def _count_in_sol(pattern: str, code: str, flags: int = re.I) -> int:
    return len(re.findall(pattern, code, flags))


def _extract_wei_literals(code: str) -> List[int]:
    """Find all large integer literals that could represent wei amounts."""
    raw = re.findall(r"\b(\d{15,})\b", code)
    return [int(v) for v in raw]


def _extract_function_names(code: str) -> List[str]:
    return re.findall(r"function\s+(\w+)\s*\(", code)


def _extract_event_names(code: str) -> List[str]:
    return re.findall(r"event\s+(\w+)\s*\(", code)


def _extract_error_names(code: str) -> List[str]:
    return re.findall(r"error\s+(\w+)\s*[;(]", code)


def _extract_state_vars(code: str) -> List[str]:
    return re.findall(
        r"(?:address|uint\d*|int\d*|bool|bytes\d*|string)\s+(?:private|public|internal)?\s*(\w+)\s*;",
        code
    )


def _score(results: List[TestResult]) -> float:
    if not results:
        return 100.0
    weights = {"critical": 3, "major": 2, "minor": 1, "info": 0}
    total_weight  = sum(weights.get(r.severity, 1) for r in results)
    passed_weight = sum(weights.get(r.severity, 1) for r in results if r.passed)
    return round(100.0 * passed_weight / total_weight, 1) if total_weight else 100.0


# ═══════════════════════════════════════════════════════════════════════════
#  Category A — Solidity Standards (syntax / language rules)
# ═══════════════════════════════════════════════════════════════════════════

def check_solidity_standards(code: str) -> List[TestResult]:
    results: List[TestResult] = []

    def _t(tid, desc, passed, severity, detail=""):
        results.append(TestResult(tid, "solidity", desc, passed, severity, detail))

    # SPDX must be line 1
    first_line = code.strip().splitlines()[0] if code.strip() else ""
    _t("SOL-001", "SPDX-License-Identifier on first line",
       "SPDX-License-Identifier" in first_line, "critical",
       f"First line: {first_line[:80]!r}")

    # Pragma version
    pragma_m = re.search(r"pragma solidity\s+([\^~]?[\d.]+)", code)
    if pragma_m:
        ver = pragma_m.group(1)
        _t("SOL-002", "Pragma version is ^0.8.16",
           "0.8" in ver, "critical", f"Found: {ver}")
    else:
        _t("SOL-002", "Pragma version is ^0.8.16", False, "critical", "No pragma found")

    # Contract definition
    _t("SOL-003", "Contract definition present",
       _find_in_sol(r"\bcontract\s+\w+", code), "critical")

    # Constructor
    _t("SOL-004", "Constructor defined",
       _find_in_sol(r"\bconstructor\s*\(", code), "major")

    # Events declared
    ev_count = _count_in_sol(r"\bevent\s+\w+\s*\(", code)
    _t("SOL-005", "At least 3 events declared",
       ev_count >= 3, "major", f"Found {ev_count} event(s)")

    # Events emitted
    emit_count = _count_in_sol(r"\bemit\s+\w+\s*\(", code)
    _t("SOL-006", "Events are emitted (emit statements)",
       emit_count >= 2, "major", f"Found {emit_count} emit(s)")

    # Custom errors (no require-with-string)
    require_str = _count_in_sol(r'require\s*\([^)]+,\s*"', code)
    custom_err  = _count_in_sol(r"\berror\s+\w+", code)
    _t("SOL-007", "Uses custom errors instead of require() strings",
       custom_err > 0 and require_str == 0, "major",
       f"custom errors={custom_err}, require-strings={require_str}")

    # No SafeMath
    _t("SOL-008", "No SafeMath import",
       not _find_in_sol(r"SafeMath", code), "minor")

    # No OpenZeppelin imports
    _t("SOL-009", "No OpenZeppelin imports",
       not _find_in_sol(r"@openzeppelin", code), "minor")

    # No selfdestruct
    _t("SOL-010", "No selfdestruct usage",
       not _find_in_sol(r"\bselfdestruct\b", code), "critical",
       "selfdestruct is forbidden in 0.8.x")

    # No tx.origin
    _t("SOL-011", "No tx.origin usage",
       not _find_in_sol(r"\btx\.origin\b", code), "major")

    # Balanced braces
    open_b  = code.count("{")
    close_b = code.count("}")
    _t("SOL-012", "Balanced braces (code not truncated)",
       open_b == close_b, "critical", f"open={open_b} close={close_b}")

    # NatSpec present
    natspec = _count_in_sol(r"///\s*@notice", code)
    _t("SOL-013", "NatSpec (@notice) present on functions",
       natspec >= 2, "minor", f"Found {natspec} @notice comments")

    # Receive function (if payable present)
    if _find_in_sol(r"\bpayable\b", code):
        _t("SOL-014", "receive() function present (contract is payable)",
           _find_in_sol(r"\breceive\s*\(\s*\)\s+external\s+payable", code), "major")

    # Enum for state machine
    _t("SOL-015", "Enum-based state machine defined",
       _find_in_sol(r"\benum\s+\w+", code), "minor",
       "State machine enums add auditability")

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Category B — Security Checks
# ═══════════════════════════════════════════════════════════════════════════

def check_security(code: str) -> List[TestResult]:
    results: List[TestResult] = []

    def _t(tid, desc, passed, severity, detail=""):
        results.append(TestResult(tid, "security", desc, passed, severity, detail))

    # Reentrancy guard — bool flag
    _t("SEC-001", "Reentrancy guard bool flag (_locked) declared",
       _find_in_sol(r"\bbool\s+private\s+_locked\b", code), "critical")

    # Reentrancy modifier
    _t("SEC-002", "noReentrant (or equivalent) modifier declared",
       _find_in_sol(r"modifier\s+\w*[Rr]eentran", code) or
       _find_in_sol(r"modifier\s+noReentrant", code), "critical")

    # noReentrant applied to ETH-transfer functions
    payable_fns = re.findall(
        r"function\s+(\w+)\s*\([^)]*\)[^{]*payable[^{]*{", code, re.I
    )
    if payable_fns:
        re_applied = any(
            _find_in_sol(rf"function\s+{fn}\s*\([^)]*\)[^{{]*noReentrant", code)
            for fn in payable_fns
        )
        _t("SEC-003", "noReentrant applied to payable functions",
           re_applied, "critical",
           f"Payable functions: {payable_fns}")

    # msg.value validation in payable functions
    if payable_fns:
        has_val_check = _find_in_sol(r"msg\.value\s*[=!<>]", code)
        _t("SEC-004", "msg.value validated in payable functions",
           has_val_check, "major")

    # Access modifiers (onlyX pattern)
    _t("SEC-005", "Access control modifiers present",
       _count_in_sol(r"modifier\s+only\w+", code) >= 1, "major",
       f"Found {_count_in_sol(r'modifier only', code)} onlyX modifiers")

    # No floating pragma
    has_float = _find_in_sol(r"pragma solidity\s+\^0\.\d+\.\d+", code)
    has_exact = _find_in_sol(r"pragma solidity\s+0\.\d+\.\d+", code)
    _t("SEC-006", "Pragma pinned or caret (not >= or <)",
       has_float or has_exact, "minor")

    # Integer overflow — no manual unchecked on sensitive ops
    unchecked_count = _count_in_sol(r"\bunchecked\s*\{", code)
    _t("SEC-007", "unchecked blocks used sparingly (≤ 5)",
       unchecked_count <= 5, "minor",
       f"Found {unchecked_count} unchecked block(s)")

    # No dangerous delegatecall
    _t("SEC-008", "No delegatecall usage",
       not _find_in_sol(r"\bdelegatecall\b", code), "major")

    # Arbitrator address stored (for dispute clauses)
    _t("SEC-009", "Arbitrator address declared as state variable",
       _find_in_sol(r"address\s+(?:payable\s+)?(?:private|public)?\s*\w*[Aa]rbitrat", code),
       "minor")

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Category C — Legal Clause Coverage
# ═══════════════════════════════════════════════════════════════════════════

def check_legal_clause_coverage(code: str, doc: ContractDocument) -> List[TestResult]:
    """Verify every legal clause type in the eContract is encoded on-chain."""
    results: List[TestResult] = []
    clause_types = {c.clause_type for c in doc.clauses}

    def _t(tid, desc, passed, severity, detail=""):
        results.append(TestResult(tid, "coverage", desc, passed, severity, detail))

    # ── Payment clauses ─────────────────────────────────────────────────
    if "payment" in clause_types:
        payment_clauses = [c for c in doc.clauses if c.clause_type == "payment"]

        _t("COV-001", "Payment logic encoded (payable function or ETH transfer)",
           _find_in_sol(r"\bpayable\b", code) or _find_in_sol(r"\.transfer\(|\.call\{", code),
           "critical")

        # Check each milestone amount is represented as wei
        for i, pc in enumerate(payment_clauses):
            if pc.amount_eth:
                wei = _eth_to_wei(pc.amount_eth)
                if wei:
                    wei_lits = _extract_wei_literals(code)
                    # Accept a 5% tolerance for gas rounding
                    found = any(abs(w - wei) / wei < 0.05 for w in wei_lits) if wei_lits else False
                    _t(f"COV-002-{i}", f"Payment amount {pc.amount_eth} encoded as wei",
                       found, "major",
                       f"Expected ~{wei} wei; literals found: {wei_lits[:5]}")

        _t("COV-003", "Payment event emitted",
           _find_in_sol(r"event\s+\w*[Pp]ayment", code) or
           _find_in_sol(r"emit\s+\w*[Pp]ayment", code), "major")

    # ── Penalty / liquidated damages ─────────────────────────────────────
    if "penalty" in clause_types:
        _t("COV-010", "Penalty logic present (penalty/deduction/fine)",
           _find_in_sol(r"penalt|deduct|fine|liquidat|damages", code), "major")

        pen_clauses = [c for c in doc.clauses if c.clause_type == "penalty"]
        for i, pc in enumerate(pen_clauses):
            if pc.amount_eth:
                wei = _eth_to_wei(pc.amount_eth)
                if wei:
                    wei_lits = _extract_wei_literals(code)
                    found = any(abs(w - wei) / wei < 0.05 for w in wei_lits) if wei_lits else False
                    _t(f"COV-011-{i}", f"Penalty amount {pc.amount_eth} encoded",
                       found, "minor",
                       f"Expected ~{wei} wei")

    # ── Expiry / term / termination ─────────────────────────────────────
    if "expiry" in clause_types:
        _t("COV-020", "Expiry/deadline stored as block.timestamp or uint deadline",
           _find_in_sol(r"block\.timestamp\s*\+|_deadline|_expiry|deadlineAt", code), "major")

        _t("COV-021", "Termination function present",
           _find_in_sol(r"function\s+terminat", code) or
           _find_in_sol(r"function\s+cancel", code), "major")

        # Check term duration encoded (look for "days" multiplier)
        exp_clauses = [c for c in doc.clauses if c.clause_type == "expiry"]
        for pc in exp_clauses:
            if pc.deadline_days:
                _t("COV-022", f"Term of {pc.deadline_days} days encoded",
                   _find_in_sol(rf"\b{pc.deadline_days}\b", code) or
                   _find_in_sol(r"\d+\s*\*\s*1\s+days", code) or
                   _find_in_sol(r"\d+\s*days\b", code),
                   "minor", f"Looking for {pc.deadline_days} day period")

    # ── Obligation / deliverable ────────────────────────────────────────
    if "obligation" in clause_types:
        _t("COV-030", "State machine transitions for obligations",
           _find_in_sol(r"_state\s*=|setState|ContractState\.", code), "major")

        _t("COV-031", "Deliverable/milestone acknowledgement function",
           _find_in_sol(r"function\s+\w*(deliver|confirm|accept|complet|approv)", code),
           "major")

    # ── Dispute resolution ──────────────────────────────────────────────
    if "dispute" in clause_types:
        _t("COV-040", "Dispute function present",
           _find_in_sol(r"function\s+\w*disput", code), "critical")

        _t("COV-041", "Arbitrator address state variable",
           _find_in_sol(r"address\s+(?:payable\s+)?(?:private|public)?\s*\w*[Aa]rbitrat", code),
           "major")

        _t("COV-042", "Dispute event emitted",
           _find_in_sol(r"event\s+\w*[Dd]isput|emit\s+\w*[Dd]isput", code), "major")

        # Verify arbitrator ETH address from eContract is encoded
        eth_addrs = doc.metadata.get("eth_addresses_found", [])
        if len(eth_addrs) >= 3:          # arbitrator is usually the 3rd address
            arb_addr = eth_addrs[-1].lower()
            addr_in_code = arb_addr in code.lower() or \
                           any(a.lower() in code.lower() for a in eth_addrs[2:])
            _t("COV-043", "Arbitrator Ethereum address from eContract encoded",
               addr_in_code, "minor",
               f"Arbitrator address: {eth_addrs[-1]}")

    # ── Confidentiality / NDA ──────────────────────────────────────────
    if "confidential" in clause_types:
        _t("COV-050", "Confidentiality acknowledged on-chain (event or bool flag)",
           _find_in_sol(r"confidential|nda|nonDisclos|non_disclos", code, re.I), "minor")

    # ── Intellectual property ──────────────────────────────────────────
    if "ip" in clause_types:
        _t("COV-060", "IP transfer / ownership acknowledged on-chain",
           _find_in_sol(r"intellectual|ownership|ip[A-Z_]|ipTransfer|copyright", code, re.I),
           "minor")

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Category D — Legal Faithfulness
# ═══════════════════════════════════════════════════════════════════════════

def check_legal_faithfulness(code: str, doc: ContractDocument) -> List[TestResult]:
    """Verify contract-specific legal fields are faithfully represented."""
    results: List[TestResult] = []

    def _t(tid, desc, passed, severity, detail=""):
        results.append(TestResult(tid, "legal", desc, passed, severity, detail))

    # ── Parties encoded ─────────────────────────────────────────────────
    for i, party in enumerate(doc.parties):
        # Wallet address present in code
        if party.wallet_hint:
            addr_found = party.wallet_hint.lower() in code.lower()
            _t(f"LEG-001-{i}", f"Party wallet address encoded: {party.role} ({party.name})",
               addr_found, "major",
               f"Address: {party.wallet_hint}")

    # ── Contract title derived ──────────────────────────────────────────
    clean_title = doc.title.lstrip("\ufeff").strip()
    title_words = [w for w in re.split(r"\W+", clean_title.lower()) if len(w) > 3]
    title_hits  = sum(1 for w in title_words if w in code.lower())
    _t("LEG-010", "Contract name reflects eContract title",
       title_hits >= 1 or _find_in_sol(r"contract\s+\w+Contract", code),
       "minor",
       f"Title words found: {title_hits}/{len(title_words)}: {title_words}")

    # ── Governing law / jurisdiction ────────────────────────────────────
    if doc.governing_law:
        law_words = [w.lower() for w in doc.governing_law.split() if len(w) > 3]
        law_found = any(w in code.lower() for w in law_words)
        _t("LEG-020", f"Governing law ({doc.governing_law}) acknowledged",
           law_found, "info",
           f"Checked keywords: {law_words}")

    # ── Effective date ──────────────────────────────────────────────────
    if doc.effective_date:
        _t("LEG-030", "Effective/start date encoded (as timestamp or comment)",
           _find_in_sol(r"effectiveDate|_startDate|startDate|createdAt", code) or
           doc.effective_date.replace(",", "").replace(" ", "") in code.replace(" ", ""),
           "info",
           f"Effective date: {doc.effective_date}")

    # ── Total contract value ─────────────────────────────────────────────
    payment_clauses = [c for c in doc.clauses if c.clause_type == "payment" and c.amount_eth]
    if payment_clauses:
        # Sum all milestone amounts
        total_eth = 0.0
        for pc in payment_clauses:
            m = re.search(r"([\d,]+(?:\.\d+)?)\s*ETH", pc.amount_eth or "", re.I)
            if m:
                total_eth += float(m.group(1).replace(",", ""))
        if total_eth > 0:
            total_wei = int(total_eth * 10**18)
            wei_lits  = _extract_wei_literals(code)
            found = any(abs(w - total_wei) / total_wei < 0.05 for w in wei_lits) if wei_lits else False
            _t("LEG-040", f"Total contract value (~{total_eth} ETH) encoded",
               found, "minor",
               f"Expected ~{total_wei} wei; found: {wei_lits[:5]}")

    # ── Late-payment penalty rate ────────────────────────────────────────
    pen_clauses = [c for c in doc.clauses if c.clause_type == "penalty"]
    for i, pc in enumerate(pen_clauses):
        if pc.deadline_days:
            _t(f"LEG-050-{i}", f"Penalty deadline ({pc.deadline_days} days) encoded",
               _find_in_sol(rf"\b{pc.deadline_days}\b", code), "minor",
               f"Penalty clause: {pc.heading}")

    # ── Confidentiality survival period ─────────────────────────────────
    conf_clauses = [c for c in doc.clauses if c.clause_type == "confidential"]
    for pc in conf_clauses:
        if pc.deadline_days:
            _t("LEG-060", f"Confidentiality survival period ({pc.deadline_days}d) noted",
               _find_in_sol(rf"\b{pc.deadline_days}\b", code), "info")

    # ── getContractState() view function ───────────────────────────────
    _t("LEG-070", "getContractState() view function present",
       _find_in_sol(r"function\s+getContractState\s*\(", code), "major")

    # ── terminate() function ─────────────────────────────────────────────
    _t("LEG-080", "terminate() function present",
       _find_in_sol(r"function\s+terminat\w*\s*\(", code), "major")

    # ── Currency is ETH (not ERC-20 only) ───────────────────────────────
    if doc.currency == "ETH":
        _t("LEG-090", "Contract operates in ETH (payable, not only ERC-20)",
           _find_in_sol(r"\bpayable\b", code), "major")

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Category E — Clause-by-clause deep check
# ═══════════════════════════════════════════════════════════════════════════

def check_clause_by_clause(code: str, doc: ContractDocument) -> List[TestResult]:
    """
    For each clause in the eContract, verify at least one representative
    keyword or construct appears in the generated Solidity.
    """
    results: List[TestResult] = []

    # Keyword maps per clause type
    CLAUSE_KEYWORDS: Dict[str, List[str]] = {
        "payment":      [r"payable", r"transfer|\.call\{", r"milestone|payment|pay"],
        "penalty":      [r"penalt|deduct|fine", r"delay|overdue", r"_penaltyRate|penaltyWei"],
        "expiry":       [r"block\.timestamp", r"_deadline|deadline", r"expired|Expired"],
        "obligation":   [r"_state\s*=", r"Completed|Active|Created", r"deliver|Deliver"],
        "dispute":      [r"disput|Disput", r"arbitrat|Arbitrat", r"escrow|Escrow"],
        "confidential": [r"confidential|nonDisclos", r"acknowledged|Acknowledged"],
        "ip":           [r"ip|IP|intellectual|ownership|copyright"],
        "general":      [r"contract\s+\w+", r"constructor"],
    }

    seen_types: set = set()
    for clause in doc.clauses:
        ctype = clause.clause_type
        if ctype in seen_types:
            continue        # one check per type is sufficient for coverage
        seen_types.add(ctype)
        keywords = CLAUSE_KEYWORDS.get(ctype, [r"contract"])

        hits = sum(1 for kw in keywords if _find_in_sol(kw, code))
        passed = hits >= max(1, len(keywords) // 2)   # at least half must hit

        results.append(TestResult(
            test_id     = f"CLS-{ctype.upper()[:6]}",
            category    = "coverage",
            description = f"Clause '{clause.heading}' [{ctype}] encoded on-chain",
            passed      = passed,
            severity    = "major" if ctype in ("payment", "penalty", "dispute") else "minor",
            detail      = f"Keywords matched {hits}/{len(keywords)}: {keywords}",
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Main runner
# ═══════════════════════════════════════════════════════════════════════════

def run_all_validations(sol_code: str, doc: ContractDocument) -> ValidationReport:
    """
    Run all validation categories and return a ValidationReport.
    Ready to merge into results.json.
    """
    all_results: List[TestResult] = []

    sol_results  = check_solidity_standards(sol_code)
    sec_results  = check_security(sol_code)
    cov_results  = check_legal_clause_coverage(sol_code, doc)
    leg_results  = check_legal_faithfulness(sol_code, doc)
    cls_results  = check_clause_by_clause(sol_code, doc)

    all_results.extend(sol_results)
    all_results.extend(sec_results)
    all_results.extend(cov_results)
    all_results.extend(leg_results)
    all_results.extend(cls_results)

    passed  = sum(1 for r in all_results if r.passed)
    failed  = len(all_results) - passed
    crits   = sum(1 for r in all_results if not r.passed and r.severity == "critical")

    report = ValidationReport(
        total_tests       = len(all_results),
        passed            = passed,
        failed            = failed,
        critical_failures = crits,
        accuracy_overall  = _score(all_results),
        accuracy_legal    = _score([r for r in all_results if r.category == "legal"]),
        accuracy_solidity = _score([r for r in all_results if r.category == "solidity"]),
        accuracy_security = _score([r for r in all_results if r.category == "security"]),
        accuracy_coverage = _score([r for r in all_results if r.category == "coverage"]),
        results           = all_results,
    )

    # Human-readable summary
    lines = [
        f"Validation: {passed}/{len(all_results)} passed | "
        f"Accuracy: {report.accuracy_overall:.1f}% | "
        f"Critical failures: {crits}"
    ]
    if crits:
        crit_names = [r.test_id for r in all_results if not r.passed and r.severity == "critical"]
        lines.append(f"Critical: {', '.join(crit_names)}")
    report.summary = " | ".join(lines)

    return report


# ═══════════════════════════════════════════════════════════════════════════
#  Pretty-printer (CLI output)
# ═══════════════════════════════════════════════════════════════════════════

SEVERITY_COLOR = {
    "critical": "\033[91m",
    "major":    "\033[93m",
    "minor":    "\033[94m",
    "info":     "\033[96m",
}
RST = "\033[0m"
GRN = "\033[92m"
RED = "\033[91m"


def print_report(report: ValidationReport) -> None:
    print(f"\n{'═'*70}")
    print("  eContract Smart Contract Validator — Results")
    print(f"{'═'*70}")

    cat_order = ["solidity", "security", "legal", "coverage"]
    grouped: Dict[str, List[TestResult]] = {c: [] for c in cat_order}
    for r in report.results:
        grouped.setdefault(r.category, []).append(r)

    for cat in cat_order:
        grp = grouped.get(cat, [])
        if not grp:
            continue
        cat_score = _score(grp)
        p = sum(1 for r in grp if r.passed)
        print(f"\n  [{cat.upper()}]  {p}/{len(grp)} passed  ({cat_score:.0f}%)")
        print(f"  {'─'*60}")
        for r in grp:
            icon = f"{GRN}✓{RST}" if r.passed else f"{RED}✗{RST}"
            sev  = SEVERITY_COLOR.get(r.severity, "") + r.severity.upper()[:4] + RST
            print(f"  {icon} {sev:30s} {r.test_id:<18} {r.description}")
            if not r.passed and r.detail:
                print(f"       → {r.detail}")

    print(f"\n{'═'*70}")
    print(f"  OVERALL ACCURACY : {report.accuracy_overall:.1f}%")
    print(f"  Solidity         : {report.accuracy_solidity:.1f}%")
    print(f"  Security         : {report.accuracy_security:.1f}%")
    print(f"  Legal            : {report.accuracy_legal:.1f}%")
    print(f"  Coverage         : {report.accuracy_coverage:.1f}%")
    print(f"  Tests            : {report.passed}/{report.total_tests} passed")
    print(f"  Critical failures: {report.critical_failures}")
    print(f"{'═'*70}\n")


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="test_contract_validator",
        description="Validate a generated Solidity contract against its source eContract.",
    )
    parser.add_argument("solidity",  help="Path to generated .sol file")
    parser.add_argument("econtract", help="Path to source eContract (.txt or .docx)")
    parser.add_argument("--json",    help="Write full JSON report to this path")
    parser.add_argument("--quiet",   action="store_true", help="Only print summary")
    args = parser.parse_args()

    sol_path = Path(args.solidity)
    src_path = Path(args.econtract)

    if not sol_path.exists():
        print(f"Error: {sol_path} not found"); sys.exit(1)
    if not src_path.exists():
        print(f"Error: {src_path} not found"); sys.exit(1)

    sol_code = sol_path.read_text(encoding="utf-8")
    doc      = extract_contract(src_path)

    report = run_all_validations(sol_code, doc)

    if not args.quiet:
        print_report(report)
    else:
        print(report.summary)

    if args.json:
        Path(args.json).write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        print(f"JSON report saved to: {args.json}")

    # Exit code reflects quality gate
    if report.critical_failures > 0:
        sys.exit(2)   # critical issues
    if report.accuracy_overall < 50.0:
        sys.exit(3)   # too inaccurate
    sys.exit(0)


if __name__ == "__main__":
    main()