"""
feedback_loop.py — Iterative LLM feedback loop for smart contract generation.

Drives the full generation → validate → diagnose → fix → regenerate cycle.
The loop only terminates when ALL of the following conditions hold:
  1. validate_solidity_output()  → zero structural issues
  2. run_all_validations()       → accuracy_overall == 100 %  AND  critical_failures == 0
  3. No compile-time errors detectable by the static analyser

If either condition fails the loop:
  a) Collects every failing test ID, every structural issue, and every
     compile-time error into a structured feedback payload.
  b) Builds a targeted repair prompt that quotes the exact failures.
  c) Sends the CURRENT (broken) code + repair prompt back to the LLM.
  d) Applies deterministic post-processing (apply_all_fixes) to the LLM reply.
  e) Re-validates. Repeats up to MAX_FEEDBACK_ITERATIONS.

After MAX_FEEDBACK_ITERATIONS the best code seen so far (highest accuracy)
is returned together with the final report, so the caller always gets a
usable artefact even if perfection was not achieved.

Usage
-----
    from feedback_loop import generate_and_refine, print_feedback_summary

    result = generate_and_refine(
        llm_client    = client,        # LLMClient instance
        doc           = contract_doc,  # ContractDocument
        system_prompt = sys_prompt,
        user_prompt   = user_prompt,
        max_iterations = 8,            # optional; default 8
        target_accuracy = 100.0,       # optional; default 100.0
        verbose       = True,
    )
    print_feedback_summary(result)
    # result.final_code    -> best Solidity code produced
    # result.final_report  -> ValidationReport
    # result.converged     -> True if 100% achieved
"""

from __future__ import annotations

import logging
import time
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from extractor import ContractDocument
from llm_client import LLMClient, extract_solidity, validate_solidity_output
from postprocessor import apply_all_fixes
from test_contract_validator import (
    ValidationReport,
    TestResult,
    run_all_validations,
)

logger = logging.getLogger("econtract.feedback_loop")

# ═══════════════════════════════════════════════════════════════════════════
#  Configuration defaults
# ═══════════════════════════════════════════════════════════════════════════

MAX_FEEDBACK_ITERATIONS = 5
TARGET_ACCURACY         = 100.0   # percentage; loop exits when reached


# ═══════════════════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class IterationResult:
    """Snapshot of one feedback-loop iteration."""
    iteration:         int
    accuracy_overall:  float
    accuracy_solidity: float
    accuracy_security: float
    accuracy_legal:    float
    accuracy_coverage: float
    total_tests:       int
    passed:            int
    failed:            int
    critical_failures: int
    structural_issues: list
    elapsed_seconds:   float
    converged:         bool


@dataclass
class FeedbackLoopResult:
    """Final result returned to the caller."""
    final_code:      str
    final_report:    ValidationReport
    iterations_used: int
    iteration_log:   list = field(default_factory=list)
    converged:       bool = False
    best_accuracy:   float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  Terminal colour helpers
# ═══════════════════════════════════════════════════════════════════════════

_RST = "\033[0m"
_BLD = "\033[1m"
_GRN = "\033[92m"
_RED = "\033[91m"
_YLW = "\033[93m"
_CYN = "\033[96m"


def _sev_emoji(sev: str) -> str:
    return {"critical": "🔴", "major": "🟠", "minor": "🟡", "info": "🔵"}.get(sev, "⚪")


def _score_color(score: float) -> str:
    if score >= 100.0:
        return _GRN
    if score >= 75.0:
        return _YLW
    return _RED


# ═══════════════════════════════════════════════════════════════════════════
#  Convergence check
# ═══════════════════════════════════════════════════════════════════════════

def _has_converged(
    report: ValidationReport,
    structural_issues: list,
    target_accuracy: float,
) -> bool:
    """All three gates must pass before we stop the loop.

    [SOFT] prefixed issues are regex heuristics and do NOT block convergence
    on their own — only hard structural errors do.
    """
    hard_issues = [i for i in structural_issues if not i.startswith("[SOFT]")]
    return (
        len(hard_issues) == 0
        and report.critical_failures == 0
        and report.accuracy_overall >= target_accuracy
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Repair-prompt builder
# ═══════════════════════════════════════════════════════════════════════════

def _format_failed_tests(report: ValidationReport) -> str:
    lines: list = []
    cat_order = ["solidity", "security", "legal", "coverage"]
    grouped: dict = {}
    for r in report.results:
        if not r.passed:
            grouped.setdefault(r.category, []).append(r)
    for cat in cat_order:
        failures = grouped.get(cat, [])
        if not failures:
            continue
        lines.append(f"\n[{cat.upper()} FAILURES]")
        for r in failures:
            lines.append(
                f"  {_sev_emoji(r.severity)} {r.test_id}  "
                f"({r.severity.upper()})  {r.description}"
            )
            if r.detail:
                lines.append(f"       Detail: {r.detail}")
    return "\n".join(lines) if lines else "  (none)"


def _format_structural_issues(issues: list) -> str:
    if not issues:
        return "  (none)"
    return "\n".join(f"  {i+1}. {iss}" for i, iss in enumerate(issues))


def _detect_stuck_errors(
    structural_issues: list[str],
    iteration_log: list,
) -> list[str]:
    """
    Return the subset of structural_issues that appeared unchanged in the
    immediately preceding iteration.  These are errors the LLM failed to fix
    and needs an explicit pinpointed hint for.
    """
    if not iteration_log:
        return []
    prev_issues = set(iteration_log[-1].structural_issues)
    return [iss for iss in structural_issues if iss in prev_issues]


def _build_stuck_hint(stuck_errors: list[str], code: str) -> str:
    """
    For each error that the LLM failed to fix in the previous iteration,
    produce a concrete, line-level hint that tells the LLM exactly what to
    change.  Falls back to a generic re-statement if no line can be found.
    """
    lines = code.splitlines()
    hints: list[str] = []

    for err in stuck_errors:
        hint_lines: list[str] = []

        # ── Wrong argument count ───────────────────────────────────────────
        m_argc = re.search(
            r'Wrong argument count.*?(\d+)\s+argument.*?expected\s+(\d+)', err, re.I
        )
        if m_argc:
            given, expected = int(m_argc.group(1)), int(m_argc.group(2))
            # Find emit lines that have more args than the event declares
            event_arity: dict[str, int] = {}
            for em in re.finditer(r'\bevent\s+(\w+)\s*\(([^)]*)\)', code):
                ev_params = [p for p in em.group(2).split(',') if p.strip()]
                event_arity[em.group(1)] = len(ev_params)
            for lineno, line in enumerate(lines, 1):
                m_emit = re.match(r'\s*emit\s+(\w+)\s*\(', line)
                if m_emit:
                    ev_name = m_emit.group(1)
                    declared = event_arity.get(ev_name)
                    if declared is not None:
                        # Crude comma count — good enough for the hint
                        approx_args = line.count(',') + 1
                        if approx_args > declared:
                            hint_lines.append(
                                f"  Line {lineno}: `{line.strip()}`\n"
                                f"  → event {ev_name} declares {declared} param(s) "
                                f"but the emit passes ~{approx_args}. "
                                f"Remove the extra argument(s) so the call matches "
                                f"the declaration exactly."
                            )

        # ── Undeclared identifier ──────────────────────────────────────────
        m_id = re.search(r'Undeclared identifier.*?["\'](\w+)["\']', err, re.I)
        if not m_id:
            m_id = re.search(r'["\'](\w+)["\']', err)
        if m_id:
            ident = m_id.group(1)
            for lineno, line in enumerate(lines, 1):
                if re.search(r'\b' + re.escape(ident) + r'\b', line):
                    hint_lines.append(
                        f"  Line {lineno}: `{line.strip()}`\n"
                        f"  → `{ident}` is used here but never declared. "
                        f"Either declare it as a state variable or replace it "
                        f"with the correct identifier."
                    )
                    break  # one example is enough

        if hint_lines:
            hints.append(f"PERSISTENT ERROR (not fixed last iteration):\n  {err}\n" + "\n".join(hint_lines))
        else:
            hints.append(
                f"PERSISTENT ERROR (not fixed last iteration):\n  {err}\n"
                f"  → This error was present in the previous iteration and was NOT "
                f"corrected. Fix it explicitly — do not leave it unchanged."
            )

    return hints


def build_repair_prompt(
    code: str,
    report: ValidationReport,
    structural_issues: list,
    iteration: int,
    target_accuracy: float,
    stuck_hints: list[str] | None = None,
) -> str:
    """Build a targeted LLM repair prompt from the current failures."""

    accuracy_block = textwrap.dedent(f"""
        Overall accuracy  : {report.accuracy_overall:.1f}%  (target: {target_accuracy:.0f}%)
        Solidity accuracy : {report.accuracy_solidity:.1f}%
        Security accuracy : {report.accuracy_security:.1f}%
        Legal accuracy    : {report.accuracy_legal:.1f}%
        Coverage accuracy : {report.accuracy_coverage:.1f}%
        Tests passed      : {report.passed}/{report.total_tests}
        Critical failures : {report.critical_failures}
    """).strip()

    stuck_block = ""
    if stuck_hints:
        stuck_block = (
            f"\n{'━'*70}\n"
            f"⚠  ERRORS THAT WERE NOT FIXED IN THE PREVIOUS ITERATION\n"
            f"{'━'*70}\n"
            + "\n\n".join(stuck_hints)
            + "\n"
        )

    return f"""You are a senior Solidity 0.8.16 auditor performing ITERATION {iteration} of an
automated feedback-fix loop. The contract below FAILED validation.
Your ONLY task: output a COMPLETE, CORRECTED Solidity file that fixes EVERY
item listed below without breaking anything that already passes.
{stuck_block}
{'━'*70}
CURRENT VALIDATION SCORES
{'━'*70}
{accuracy_block}

{'━'*70}
STRUCTURAL / COMPILE ERRORS TO FIX
{'━'*70}
{_format_structural_issues(structural_issues)}

{'━'*70}
FAILED TEST CASES TO FIX
{'━'*70}
{_format_failed_tests(report)}

{'━'*70}
MANDATORY SELF-CHECK BEFORE OUTPUT
{'━'*70}
□  SPDX-License-Identifier on line 1
□  pragma solidity ^0.8.16; on line 2
□  bool private _locked; at contract scope (NOT inside modifier)
□  modifier noReentrant() declared with ZERO parameters
□  noReentrant applied to every ETH-transferring function
□  Zero require(condition, "string") — custom errors only
□  Every state-changing function emits an event
□  >= 8 /// @notice NatSpec comments
□  getContractState() returns NO mapping types
□  Balanced braces {{ }}
□  No OpenZeppelin / SafeMath imports
□  Event params: NO memory / calldata / storage keywords
□  EFFECTIVE_DATE = integer literal (NOT block.timestamp)
□  Every function definition starts with `function` keyword
□  dispute() function + _arbitrator state var + DisputeRaised event
□  enum ContractState with >= 5 states
□  acknowledgeDelivery() or confirmMilestone() present
□  >= 2 `modifier onlyX` access-control declarations
□  >= 1 external payable function AND receive() fallback
□  calculatePenalty() NOT marked view; uses uint256 principal param
□  uint256 public immutable startDate declared + assigned in constructor
□  string public constant GOVERNING_LAW = "..." declared
□  All revert targets declared as: error Name(...);
□  All emit targets declared as: event Name(...);
□  EMIT ARITY CHECK: count the parameters in every `event Name(...)` declaration.
   Every `emit Name(...)` call MUST pass EXACTLY that many arguments — no more, no less.
□  UNDECLARED IDENTIFIER CHECK: every identifier in every modifier body
   MUST be a declared state var (_partyA, _partyB, _arbitrator, etc.).
   NEVER use company/entity names (CamelCase) directly — use _partyA/_partyB.
□  CONFIDENTIALITY (COV-050): if contract has confidentiality clause,
   the word 'nonDisclos' or 'confidential' MUST appear in the code.
   Required: bool private _confidentialityAcknowledged;
             event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);
             function acknowledgeNonDisclosure() external onlyParties {{ ... }}

{'━'*70}
CURRENT (FAILING) CONTRACT — REPAIR THIS
{'━'*70}
{code}

{'━'*70}
OUTPUT RULES
{'━'*70}
• Output ONLY valid Solidity source code — no markdown fences, no prose.
• Preserve all working logic. Fix ONLY the failing items above.
• The repaired contract MUST reach {target_accuracy:.0f}% accuracy.
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Iteration banner
# ═══════════════════════════════════════════════════════════════════════════

def _print_iteration_banner(
    it: int,
    max_it: int,
    elapsed: float,
    report: ValidationReport,
    structural_issues: list,
    converged: bool,
) -> None:
    sep = "═" * 70
    status = (
        f"{_GRN}✓ CONVERGED{_RST}"
        if converged
        else f"{_RED}✗ NEEDS REPAIR{_RST}"
    )
    print(f"\n{sep}")
    print(f"{_BLD}  Feedback Loop — Iteration {it}/{max_it}  |  {status}{_RST}")
    print(sep)
    sc = _score_color(report.accuracy_overall)
    print(
        f"  Overall  : {sc}{report.accuracy_overall:.1f}%{_RST}"
        f"  |  Solidity: {report.accuracy_solidity:.1f}%"
        f"  |  Security: {report.accuracy_security:.1f}%"
    )
    print(
        f"  Legal    : {report.accuracy_legal:.1f}%"
        f"  |  Coverage: {report.accuracy_coverage:.1f}%"
        f"  |  Elapsed: {elapsed:.1f}s"
    )
    print(
        f"  Tests    : {report.passed}/{report.total_tests} passed"
        f"  |  Critical failures: {report.critical_failures}"
    )
    if structural_issues:
        print(f"\n  {_RED}Structural issues ({len(structural_issues)}):{_RST}")
        for iss in structural_issues[:5]:
            print(f"    • {iss}")
        if len(structural_issues) > 5:
            print(f"    … and {len(structural_issues) - 5} more")
    failures = [r for r in report.results if not r.passed]
    if failures:
        print(f"\n  {_RED}Failed tests ({len(failures)}):{_RST}")
        for r in failures[:10]:
            detail = f"\n        → {r.detail}" if r.detail else ""
            print(f"    {_sev_emoji(r.severity)} [{r.test_id}] {r.description}{detail}")
        if len(failures) > 10:
            print(f"    … and {len(failures) - 10} more")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════
#  Core feedback loop
# ═══════════════════════════════════════════════════════════════════════════

def run_feedback_loop(
    llm_client: LLMClient,
    doc: ContractDocument,
    initial_code: str,
    system_prompt: str,
    max_iterations: int = MAX_FEEDBACK_ITERATIONS,
    target_accuracy: float = TARGET_ACCURACY,
    verbose: bool = True,
) -> FeedbackLoopResult:
    """
    Iteratively validate → repair until target_accuracy is reached or
    max_iterations is exhausted.

    Parameters
    ----------
    llm_client      : Configured LLMClient (Ollama backend).
    doc             : Parsed ContractDocument from extractor.py.
    initial_code    : First LLM output, already passed through apply_all_fixes().
    system_prompt   : System prompt used for all LLM repair calls.
    max_iterations  : Hard cap on iterations (default 8).
    target_accuracy : Accuracy % required to stop (default 100.0).
    verbose         : Print progress banners to stdout.

    Returns
    -------
    FeedbackLoopResult
    """
    best_code: str = initial_code
    best_accuracy: float = 0.0
    best_report: Optional[ValidationReport] = None
    iteration_log: list = []
    current_code: str = initial_code

    if verbose:
        print(f"\n{_CYN}{'━'*70}")
        print(
            f"  🔁  Smart Contract Feedback Loop  "
            f"(target: {target_accuracy:.0f}%  |  max iterations: {max_iterations})"
        )
        print(f"{'━'*70}{_RST}")

    for iteration in range(1, max_iterations + 1):
        t0 = time.time()

        # ── 1. Structural / compile-time validation ────────────────────────
        _ok, structural_issues = validate_solidity_output(current_code)

        # ── 2. Full test-suite validation ──────────────────────────────────
        report = run_all_validations(current_code, doc)
        elapsed = time.time() - t0

        # ── 3. Convergence check ───────────────────────────────────────────
        converged = _has_converged(report, structural_issues, target_accuracy)

        # ── 4. Track best result seen so far ──────────────────────────────
        if report.accuracy_overall > best_accuracy or (
            report.accuracy_overall == best_accuracy and len(structural_issues) == 0
        ):
            best_code     = current_code
            best_accuracy = report.accuracy_overall
            best_report   = report

        # ── 5. Record iteration snapshot ──────────────────────────────────
        iter_result = IterationResult(
            iteration         = iteration,
            accuracy_overall  = report.accuracy_overall,
            accuracy_solidity = report.accuracy_solidity,
            accuracy_security = report.accuracy_security,
            accuracy_legal    = report.accuracy_legal,
            accuracy_coverage = report.accuracy_coverage,
            total_tests       = report.total_tests,
            passed            = report.passed,
            failed            = report.failed,
            critical_failures = report.critical_failures,
            structural_issues = list(structural_issues),
            elapsed_seconds   = elapsed,
            converged         = converged,
        )
        iteration_log.append(iter_result)

        if verbose:
            _print_iteration_banner(
                iteration, max_iterations, elapsed,
                report, structural_issues, converged,
            )

        # ── 6. Exit early on convergence ───────────────────────────────────
        if converged:
            if verbose:
                print(f"\n{_GRN}{'━'*70}")
                print(
                    f"    Converged after {iteration} iteration(s)!  "
                    f"Accuracy: {report.accuracy_overall:.1f}%"
                )
                print(f"{'━'*70}{_RST}\n")
            return FeedbackLoopResult(
                final_code      = current_code,
                final_report    = report,
                iterations_used = iteration,
                iteration_log   = iteration_log,
                converged       = True,
                best_accuracy   = report.accuracy_overall,
            )

        # ── 7. Build and send repair prompt ────────────────────────────────
        if iteration == max_iterations:
            break   # exhausted — fall through to return best

        if verbose:
            has_soft_only = all(i.startswith("[SOFT]") for i in structural_issues)
            reason = []
            if report.accuracy_overall < target_accuracy:
                reason.append(f"accuracy {report.accuracy_overall:.1f}% < {target_accuracy:.0f}%")
            if report.critical_failures:
                reason.append(f"{report.critical_failures} critical failure(s)")
            if structural_issues and not has_soft_only:
                reason.append(f"{len(structural_issues)} structural issue(s)")
            reason_str = " | ".join(reason) if reason else "structural issues remain"
            print(
                f"\n{_YLW}  → {reason_str}.  "
                f"Sending repair prompt (iteration {iteration + 1})…{_RST}"
            )

        repair_prompt = build_repair_prompt(
            code              = current_code,
            report            = report,
            structural_issues = structural_issues,
            iteration         = iteration + 1,
            target_accuracy   = target_accuracy,
            stuck_hints       = _build_stuck_hint(
                _detect_stuck_errors(structural_issues, iteration_log[:-1]),
                current_code,
            ) or None,
        )

        # ── 8. LLM repair call ─────────────────────────────────────────────
        try:
            raw_fixed   = llm_client._backend.generate(system_prompt, repair_prompt)
            fixed_code  = extract_solidity(raw_fixed)
            fixed_code  = apply_all_fixes(fixed_code, doc)
            current_code = fixed_code
            logger.info(f"Iteration {iteration + 1}: LLM repair successful.")
        except Exception as exc:
            logger.error(f"LLM call failed on iteration {iteration + 1}: {exc}")
            if verbose:
                print(f"{_RED}  ✗ LLM error on iteration {iteration + 1}: {exc}{_RST}")
            # Keep current_code unchanged; re-validate on next round
            continue

    # ── Exhausted all iterations ────────────────────────────────────────────
    if verbose:
        print(f"\n{_YLW}{'━'*70}")
        print(f"    Max iterations ({max_iterations}) reached.")
        print(f"  Best accuracy achieved: {best_accuracy:.1f}%")
        print(f"  Returning best contract seen so far.")
        print(f"{'━'*70}{_RST}\n")

    if best_report is None:
        best_report = run_all_validations(best_code, doc)

    return FeedbackLoopResult(
        final_code      = best_code,
        final_report    = best_report,
        iterations_used = max_iterations,
        iteration_log   = iteration_log,
        converged       = False,
        best_accuracy   = best_accuracy,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Convenience wrapper — generate from scratch then refine
# ═══════════════════════════════════════════════════════════════════════════

def generate_and_refine(
    llm_client: LLMClient,
    doc: ContractDocument,
    system_prompt: str,
    user_prompt: str,
    max_iterations: int = MAX_FEEDBACK_ITERATIONS,
    target_accuracy: float = TARGET_ACCURACY,
    verbose: bool = True,
) -> FeedbackLoopResult:
    """
    Full pipeline:
      1. Call LLM with system_prompt + user_prompt → initial Solidity.
      2. Apply deterministic fixes (apply_all_fixes).
      3. Run feedback loop until target_accuracy or max_iterations.

    Returns FeedbackLoopResult.
    """
    if verbose:
        print(f"{_CYN}  Step 1 — Initial contract generation…{_RST}")

    t0 = time.time()
    raw          = llm_client._backend.generate(system_prompt, user_prompt)
    initial_code = extract_solidity(raw)
    initial_code = apply_all_fixes(initial_code, doc)

    if verbose:
        print(f"  Initial generation complete in {time.time() - t0:.1f}s")

    return run_feedback_loop(
        llm_client      = llm_client,
        doc             = doc,
        initial_code    = initial_code,
        system_prompt   = system_prompt,
        max_iterations  = max_iterations,
        target_accuracy = target_accuracy,
        verbose         = verbose,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Pretty-print summary
# ═══════════════════════════════════════════════════════════════════════════

def print_feedback_summary(result: FeedbackLoopResult) -> None:
    """Print a human-readable post-run summary."""
    sep = "═" * 70
    r   = result.final_report
    print(f"\n{sep}")
    print("  FEEDBACK LOOP — FINAL SUMMARY")
    print(sep)
    status = " CONVERGED" if result.converged else " MAX ITERATIONS REACHED"
    print(f"  Status          : {status}")
    print(f"  Iterations used : {result.iterations_used}")
    print(f"  Best accuracy   : {result.best_accuracy:.1f}%")
    print(f"\n  FINAL SCORES")
    print(f"  {'─'*40}")
    print(f"  Overall  : {r.accuracy_overall:.1f}%")
    print(f"  Solidity : {r.accuracy_solidity:.1f}%")
    print(f"  Security : {r.accuracy_security:.1f}%")
    print(f"  Legal    : {r.accuracy_legal:.1f}%")
    print(f"  Coverage : {r.accuracy_coverage:.1f}%")
    print(f"\n  Tests    : {r.passed}/{r.total_tests} passed")
    print(f"  Critical : {r.critical_failures} failures")

    print(f"\n  ITERATION HISTORY")
    print(f"  {'─'*40}")
    print(f"  {'#':>3}  {'Overall':>8}  {'SOL':>6}  {'SEC':>6}  "
          f"{'LEG':>6}  {'COV':>6}  {'Crit':>5}  {'t(s)':>6}")
    for it in result.iteration_log:
        mark = " ✓" if it.converged else ""
        print(
            f"  {it.iteration:>3}  {it.accuracy_overall:>7.1f}%  "
            f"{it.accuracy_solidity:>5.1f}%  {it.accuracy_security:>5.1f}%  "
            f"{it.accuracy_legal:>5.1f}%  {it.accuracy_coverage:>5.1f}%  "
            f"{it.critical_failures:>5}  {it.elapsed_seconds:>5.1f}s{mark}"
        )

    if not result.converged:
        failures = [tr for tr in r.results if not tr.passed]
        if failures:
            print(f"\n  REMAINING FAILURES ({len(failures)})")
            print(f"  {'─'*40}")
            for tr in failures:
                detail = f"\n      → {tr.detail}" if tr.detail else ""
                print(f"  {_sev_emoji(tr.severity)} [{tr.test_id}] {tr.description}{detail}")
    print(f"{sep}\n")