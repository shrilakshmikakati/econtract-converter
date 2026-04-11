#!/usr/bin/env python3
"""
econtract_converter.py — Production-ready CLI tool  (v3.0 — with Feedback Loop)
Converts electronic contracts (.docx / .txt) → Solidity 0.8.16 smart contracts
using a local LLM (Ollama + qwen2.5-coder:7b by default).

WHAT'S NEW in v3.0
==================
  • Full feedback loop integrated (feedback_loop.py).
  • After every LLM generation the contract is compiled with `solc` in a real
    bash subprocess.  Any compiler errors / warnings are injected back into the
    LLM repair prompt so the model can fix them precisely.
  • The loop only exits when ALL three gates pass simultaneously:
      1. validate_solidity_output()  → zero structural issues
      2. run_all_validations()       → accuracy_overall >= TARGET  AND
                                       critical_failures == 0
      3. `solc --strict-assembly`    → zero errors  (warnings allowed)
  • Best-seen artefact is always returned even if convergence is not reached.

Results folder contains ONLY:
  <contract_name>.sol   — the final Solidity smart contract
  results.json          — full metadata + accuracy scores + per-test results

Usage:
    python econtract_converter.py <input_file> [options]

Examples:
    python econtract_converter.py contract.docx
    python econtract_converter.py contract.txt --model mistral:latest --output ./out
    python econtract_converter.py contract.docx --dry-run
    python econtract_converter.py contract.txt --backend openai --model gpt-4o
    python econtract_converter.py contract.txt --skip-validation
    python econtract_converter.py contract.docx --max-iterations 12 --target-accuracy 95
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

# ── Local modules ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))

from extractor import extract_contract, SUPPORTED_EXTENSIONS
from prompt_builder import build_user_prompt, get_system_prompt, build_validation_prompt
from llm_client import LLMClient, LLMConfig, validate_solidity_output
from postprocessor import (
    apply_all_fixes,
    save_solidity,
    save_report,
    run_contract_validation,
)
from feedback_loop import (
    generate_and_refine,
    run_feedback_loop,
    print_feedback_summary,
    FeedbackLoopResult,
    build_repair_prompt,
    MAX_FEEDBACK_ITERATIONS,
    TARGET_ACCURACY,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Logging
# ═══════════════════════════════════════════════════════════════════════════

def setup_logging(verbose: bool, log_file: Path = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


logger = logging.getLogger("econtract")


# ═══════════════════════════════════════════════════════════════════════════
#  Banner
# ═══════════════════════════════════════════════════════════════════════════

BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║   eContract → Smart Contract Converter  v3.0                     ║
║   Solidity 0.8.16  |  Local LLM (Ollama)     ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ANSI helpers
_RST = "\033[0m";  _GRN = "\033[92m";  _RED = "\033[91m";  _YLW = "\033[93m"
_CYN = "\033[96m"; _BLD = "\033[1m"


# ═══════════════════════════════════════════════════════════════════════════
#  solc compile helper
# ═══════════════════════════════════════════════════════════════════════════

def _find_solc() -> Optional[str]:
    """Return the path to solc if it is available on PATH, else None."""
    return shutil.which("solc")


def compile_with_solc(solidity_code: str) -> Tuple[bool, str]:
    """
    Compile *solidity_code* with the system solc binary.

    Returns
    -------
    (success, output)
        success  – True when solc exits with code 0 (zero errors).
        output   – Full stdout+stderr combined (useful for the repair prompt).
    """
    solc_path = _find_solc()
    if solc_path is None:
        return True, ""   

    with tempfile.NamedTemporaryFile(
        suffix=".sol", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(solidity_code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [solc_path, "--no-color", tmp_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = (result.stdout + result.stderr).strip()
        success  = result.returncode == 0
        return success, combined
    except subprocess.TimeoutExpired:
        return False, "solc compilation timed out."
    except Exception as exc:
        return False, f"solc subprocess error: {exc}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _extract_solc_errors(solc_output: str) -> list:
    """
    Parse solc output and return a list of error strings (not warnings).
    Lines that contain 'Error:' are treated as hard errors.
    """
    errors = []
    for line in solc_output.splitlines():
        if "Error:" in line or "error:" in line.lower():
            errors.append(line.strip())
    return errors


# ═══════════════════════════════════════════════════════════════════════════
#  Solc-aware feedback loop
# ═══════════════════════════════════════════════════════════════════════════

def _has_converged_with_solc(
    report,
    structural_issues: list,
    solc_errors: list,
    target_accuracy: float,
) -> bool:
    """
    Extended convergence check that also requires a clean solc compile.
    All three gates must pass:
      1. No structural issues (validate_solidity_output)
      2. No critical validation failures AND accuracy >= target
      3. No solc compile errors
    """
    return (
        len(structural_issues) == 0
        and report.critical_failures == 0
        and report.accuracy_overall >= target_accuracy
        and len(solc_errors) == 0
    )


def run_pipeline_with_feedback(
    llm_client: LLMClient,
    doc,
    system_prompt: str,
    user_prompt: str,
    max_iterations: int,
    target_accuracy: float,
    verbose: bool = True,
) -> FeedbackLoopResult:
    """
    Drop-in replacement for generate_and_refine() that adds a real
    solc compile step inside the loop.

    Flow per iteration
    ------------------
    1. validate_solidity_output()  → structural issues
    2. run_all_validations()       → ValidationReport
    3. compile_with_solc()         → solc errors
    4. Check convergence (all three gates)
    5. If not converged: build repair prompt (includes solc errors) → LLM → fix
    6. Repeat until converged OR max_iterations reached
    """
    from llm_client import extract_solidity
    from postprocessor import apply_all_fixes
    from test_contract_validator import run_all_validations
    from feedback_loop import (
        IterationResult,
        FeedbackLoopResult,
        _print_iteration_banner,
        _sev_emoji,
    )

    # ── Initial generation ────────────────────────────────────────────────
    if verbose:
        print(f"\n{_CYN}{'━'*70}")
        print(
            f"    Smart Contract Feedback Loop  "
            f"(target: {target_accuracy:.0f}%  |  max iterations: {max_iterations})"
        )
        print(f"{'━'*70}{_RST}")
        print(f"{_CYN}  Step 0 — Initial contract generation…{_RST}")

    t_gen = time.time()
    raw          = llm_client._backend.generate(system_prompt, user_prompt)
    initial_code = extract_solidity(raw)
    initial_code = apply_all_fixes(initial_code, doc)

    if verbose:
        print(f"  Initial generation complete in {time.time() - t_gen:.1f}s  "
              f"({len(initial_code):,} chars)")

    # ── Loop state ────────────────────────────────────────────────────────
    current_code   = initial_code
    best_code      = initial_code
    best_accuracy  = 0.0
    best_report    = None
    iteration_log  = []

    for iteration in range(1, max_iterations + 1):
        t0 = time.time()

        # Gate 1: structural / syntax
        _ok, structural_issues = validate_solidity_output(current_code)

        # Gate 2: full test-suite
        report = run_all_validations(current_code, doc)

        # Gate 3: real solc compile
        solc_ok, solc_output = compile_with_solc(current_code)
        solc_errors = _extract_solc_errors(solc_output) if not solc_ok else []

        elapsed   = time.time() - t0
        converged = _has_converged_with_solc(
            report, structural_issues, solc_errors, target_accuracy
        )

        # Track best
        if report.accuracy_overall > best_accuracy or (
            report.accuracy_overall == best_accuracy and len(structural_issues) == 0
        ):
            best_code     = current_code
            best_accuracy = report.accuracy_overall
            best_report   = report

        # Record snapshot
        iter_snap = IterationResult(
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
            structural_issues = list(structural_issues) + solc_errors,
            elapsed_seconds   = elapsed,
            converged         = converged,
        )
        iteration_log.append(iter_snap)

        if verbose:
            _print_iteration_banner(
                iteration, max_iterations, elapsed,
                report, structural_issues + solc_errors, converged,
            )
            if solc_errors:
                print(f"\n  {_RED}solc compile errors ({len(solc_errors)}):{_RST}")
                for err in solc_errors[:8]:
                    print(f"    • {err}")
                if len(solc_errors) > 8:
                    print(f"    … and {len(solc_errors) - 8} more")

        # ── Converged → done ───────────────────────────────────────────────
        if converged:
            if verbose:
                print(f"\n{_GRN}{'━'*70}")
                print(
                    f"    Converged after {iteration} iteration(s)!  "
                    f"Accuracy: {report.accuracy_overall:.1f}%  |  solc: ✓"
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

        # ── Last iteration → fall through ──────────────────────────────────
        if iteration == max_iterations:
            break

        # ── Build repair prompt ────────────────────────────────────────────
        if verbose:
            print(
                f"\n{_YLW}  → Accuracy {report.accuracy_overall:.1f}% / "
                f"solc errors {len(solc_errors)}.  "
                f"Sending repair prompt (iteration {iteration + 1})…{_RST}"
            )

        # Merge solc errors into the structural-issues list so build_repair_prompt
        # sees them and forwards them verbatim to the LLM.
        combined_issues = list(structural_issues) + (
            [f"[solc] {e}" for e in solc_errors]
        )
        # Also append raw solc output block for full context
        if solc_errors:
            combined_issues.append(
                f"\n[SOLC FULL OUTPUT]\n{solc_output[:3000]}"
            )

        repair_prompt = build_repair_prompt(
            code              = current_code,
            report            = report,
            structural_issues = combined_issues,
            iteration         = iteration + 1,
            target_accuracy   = target_accuracy,
        )

        # ── LLM repair call ────────────────────────────────────────────────
        try:
            raw_fixed    = llm_client._backend.generate(system_prompt, repair_prompt)
            fixed_code   = extract_solidity(raw_fixed)
            fixed_code   = apply_all_fixes(fixed_code, doc)
            current_code = fixed_code
            logger.info(f"Iteration {iteration + 1}: LLM repair successful.")
        except Exception as exc:
            logger.error(f"LLM call failed on iteration {iteration + 1}: {exc}")
            if verbose:
                print(f"{_RED}  ✗ LLM error on iteration {iteration + 1}: {exc}{_RST}")
            # Keep current_code; retry next round
            continue

    # ── Exhausted all iterations ───────────────────────────────────────────
    if verbose:
        print(f"\n{_YLW}{'━'*70}")
        print(f"  ⚠️  Max iterations ({max_iterations}) reached.")
        print(f"  Best accuracy achieved: {best_accuracy:.1f}%")
        print(f"  Returning best contract seen so far.")
        print(f"{'━'*70}{_RST}\n")

    if best_report is None:
        from test_contract_validator import run_all_validations
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
#  Core pipeline
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline_for_file(input_file: Path, args: argparse.Namespace) -> int:
    """
    Full conversion pipeline for a single file.

    Pipeline steps
    --------------
    1/6  Extract & pre-process contract text
    2/6  Build LLM prompts
    3/6  Initial LLM generation  (step 0 inside feedback loop)
    4/6  Feedback loop  → generate → solc compile → validate → repair → repeat
    5/6  Save outputs (.sol + results.json)
    6/6  Print summary

    Exit codes
    ----------
    0  success — all validations passed + solc clean
    1  hard failure (extraction / LLM unreachable)
    2  generated but structural issues remain
    3  generated but legal / compliance quality gate failed
    """
    t0 = time.monotonic()

    input_path = input_file.resolve()
    output_dir = Path(args.output).resolve() / input_path.stem

    # Reset logging for each file
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    setup_logging(args.verbose)
    file_logger = logging.getLogger(f"econtract.{input_path.stem}")

    file_logger.info("━" * 62)
    file_logger.info(f"Processing file : {input_path}")
    file_logger.info(f"Output dir      : {output_dir}")
    file_logger.info(f"LLM model       : {args.model}")
    file_logger.info(f"Backend         : {args.backend}")
    file_logger.info(f"Max iterations  : {args.max_iterations}")
    file_logger.info(f"Target accuracy : {args.target_accuracy}%")
    solc_path = _find_solc()
    if solc_path:
        file_logger.info(f"solc            : {solc_path}  ✓")
    else:
        file_logger.warning(
            "solc            : NOT FOUND on PATH — compile check disabled. "
            "Install with: pip install solc-select && solc-select install 0.8.16 && solc-select use 0.8.16"
        )

    # ── Step 1: Validate input ────────────────────────────────────────────
    if not input_path.exists():
        file_logger.error(f"File not found: {input_path}")
        return 1
    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        file_logger.error(
            f"Unsupported file type '{input_path.suffix}'. "
            f"Accepted: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
        return 1

    # ── Step 2: Extract contract ──────────────────────────────────────────
    file_logger.info("━" * 62)
    file_logger.info("STEP 1/6  Extracting & preprocessing contract…")
    try:
        doc = extract_contract(input_path)
    except Exception as e:
        file_logger.error(f"Extraction failed: {e}")
        return 1

    file_logger.info(f"  Title      : {doc.title}")
    file_logger.info(f"  Parties    : {len(doc.parties)}")
    file_logger.info(f"  Clauses    : {len(doc.clauses)}")
    file_logger.info(f"  Characters : {doc.metadata.get('char_count', 0):,}")
    for p in doc.parties:
        w = f" ({p.wallet_hint})" if p.wallet_hint else ""
        file_logger.info(f"    - {p.role}: {p.name}{w}")

    # ── Step 3: Build prompts ─────────────────────────────────────────────
    file_logger.info("━" * 62)
    file_logger.info("STEP 2/6  Building LLM prompts…")
    system_prompt = get_system_prompt()
    user_prompt   = build_user_prompt(doc)

    if args.dry_run:
        file_logger.info("DRY RUN: skipping LLM call. Printing prompt excerpt.")
        print("\n" + user_prompt[:2000])
        return 0

    # ── Step 4: LLM setup ────────────────────────────────────────────────
    file_logger.info("━" * 62)
    file_logger.info("STEP 3/6  Connecting to LLM backend…")

    cfg = LLMConfig(
        model       = args.model,
        base_url    = args.ollama_url,
        backend     = args.backend,
        temperature = args.temperature,
        api_key     = os.environ.get("OPENAI_API_KEY"),
    )
    client = LLMClient(cfg)

    if not client.health_check():
        file_logger.error(
            f"Cannot reach LLM backend at {args.ollama_url}. "
            "Make sure Ollama is running: `ollama serve`"
        )
        return 1

    if args.backend == "ollama":
        try:
            client.ensure_model()
        except RuntimeError as e:
            file_logger.warning(str(e))

    # ── Step 5: Feedback loop (generate → compile → validate → repair) ────
    file_logger.info("━" * 62)
    file_logger.info(
        f"STEP 4/6  Running feedback loop  "
        f"(max {args.max_iterations} iterations, "
        f"target {args.target_accuracy}% accuracy)…"
    )

    try:
        loop_result: FeedbackLoopResult = run_pipeline_with_feedback(
            llm_client      = client,
            doc             = doc,
            system_prompt   = system_prompt,
            user_prompt     = user_prompt,
            max_iterations  = args.max_iterations,
            target_accuracy = args.target_accuracy,
            verbose         = True,
        )
    except RuntimeError as e:
        file_logger.error(f"Feedback loop failed: {e}")
        return 1

    print_feedback_summary(loop_result)

    final_code       = loop_result.final_code
    validation_report = loop_result.final_report
    _ok, final_issues = validate_solidity_output(final_code)

    elapsed = time.monotonic() - t0

    # ── Final solc compile (authoritative check on the saved code) ────────
    file_logger.info("━" * 62)
    file_logger.info("STEP 5/6  Final solc compile check on selected contract…")
    solc_success, solc_output = compile_with_solc(final_code)
    if solc_success:
        file_logger.info("  ✓ solc compile: PASSED (zero errors)")
    else:
        solc_errors = _extract_solc_errors(solc_output)
        file_logger.warning(
            f"  ⚠  solc compile: {len(solc_errors)} error(s) remain after loop"
        )
        for err in solc_errors:
            file_logger.warning(f"     • {err}")
        if not loop_result.converged:
            file_logger.warning(
                "  The best contract produced still has compile errors. "
                "Consider increasing --max-iterations or --target-accuracy."
            )

    # ── Step 6: Save outputs ──────────────────────────────────────────────
    file_logger.info("━" * 62)
    file_logger.info("STEP 6/6  Saving outputs…")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sol_path = save_solidity(final_code, doc, output_dir, filename=input_path.stem)
    rep_path = save_report(
        doc, sol_path, final_issues, output_dir, elapsed,
        validation_report=validation_report,
    )

    # Ensure ONLY the two expected files are present
    expected = {sol_path.name, "results.json"}
    for f in output_dir.iterdir():
        if f.name not in expected:
            file_logger.warning(f"  Unexpected file cleaned up: {f.name}")
            f.unlink()

    # ── Summary ───────────────────────────────────────────────────────────
    file_logger.info("━" * 62)
    file_logger.info(f"CONVERSION COMPLETE  →  {input_path.name}")
    file_logger.info(f"   Solidity   : {sol_path}")
    file_logger.info(f"   Report     : results.json")
    file_logger.info(f"   Elapsed    : {elapsed:.1f}s")
    file_logger.info(f"   Converged  : {'YES ✓' if loop_result.converged else 'NO (best saved)'}")
    file_logger.info(
        f"   Accuracy   : {loop_result.best_accuracy:.1f}%  "
        f"({validation_report.passed}/{validation_report.total_tests} tests)"
    )
    file_logger.info(f"   solc       : {'✓ clean' if solc_success else '⚠ errors'}")
    file_logger.info("━" * 62)

    if args.print_code:
        print("\n" + "═" * 70)
        print(f"GENERATED SOLIDITY — {input_path.name}")
        print("═" * 70)
        print(final_code)

    # Exit code
    if validation_report and validation_report.critical_failures > 0:
        return 3
    if not _ok or not solc_success:
        return 2
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  Multi-file runner
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline(args: argparse.Namespace) -> int:
    print(BANNER)
    overall_exit = 0
    for input_file_str in args.inputs:
        code = run_pipeline_for_file(Path(input_file_str), args)
        if code > overall_exit:
            overall_exit = code
    logger.info(f"Processed {len(args.inputs)} file(s).  Overall exit code: {overall_exit}")
    return overall_exit


# ═══════════════════════════════════════════════════════════════════════════
#  CLI argument parser
# ═══════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="econtract_converter",
        description=(
            "Convert eContracts (.docx/.txt) to Solidity 0.8.16 smart contracts "
            "with an integrated generate → compile → validate → repair feedback loop."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("inputs", nargs="+",
                   help="Path(s) to the eContract file(s) (.docx or .txt)")
    p.add_argument("-o", "--output", default="./Results",
                   help="Output root directory (default: ./Results)")
    p.add_argument("-m", "--model",
                   default=os.environ.get("LLM_MODEL", "llama3.1:8b"),
                   help="LLM model name (default: llama3.1:8b)")
    p.add_argument("--backend", choices=["ollama", "openai"],
                   default=os.environ.get("LLM_BACKEND", "ollama"))
    p.add_argument("--ollama-url",
                   default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    p.add_argument("--temperature", type=float, default=0.1,
                   help="LLM sampling temperature (default: 0.1)")

    # ── Feedback loop controls ─────────────────────────────────────────────
    p.add_argument(
        "--max-iterations", type=int,
        default=int(os.environ.get("FEEDBACK_MAX_ITER", MAX_FEEDBACK_ITERATIONS)),
        help=f"Max feedback-loop iterations (default: {MAX_FEEDBACK_ITERATIONS})",
    )
    p.add_argument(
        "--target-accuracy", type=float,
        default=float(os.environ.get("FEEDBACK_TARGET", TARGET_ACCURACY)),
        help=f"Accuracy %% to aim for (default: {TARGET_ACCURACY})",
    )

    # ── Legacy / misc ──────────────────────────────────────────────────────
    p.add_argument("--skip-validation", action="store_true",
                   help="Skip the legal/compliance test suite (not recommended)")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract & build prompt only, do not call LLM")
    p.add_argument("--print-code", action="store_true",
                   help="Print the final Solidity to stdout after saving")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug-level logging")
    return p


def main() -> None:
    setup_logging(verbose=True)
    parser = build_parser()
    args   = parser.parse_args()
    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()