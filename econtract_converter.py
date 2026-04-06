#!/usr/bin/env python3
"""
econtract_converter.py — Production-ready CLI tool
Converts electronic contracts (.docx / .txt) → Solidity 0.8.16 smart contracts
using a local LLM (Ollama + qwen2.5-coder:7b by default).

Usage:
    python econtract_converter.py <input_file> [options]

Examples:
    python econtract_converter.py contract.docx
    python econtract_converter.py contract.txt --model qwen2.5-coder:7b --output ./out
    python econtract_converter.py contract.docx --dry-run
    python econtract_converter.py contract.txt --backend openai --model gpt-4o
    python econtract_converter.py contract.txt --skip-validation
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# ── Local modules ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))

from extractor import extract_contract, SUPPORTED_EXTENSIONS
from prompt_builder import build_user_prompt, get_system_prompt, build_validation_prompt
from llm_client import LLMClient, LLMConfig, validate_solidity_output
from postprocessor import (
    apply_all_fixes,
    save_solidity,
    save_report,
    save_human_readable_summary,
    run_contract_validation,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Logging
# ═══════════════════════════════════════════════════════════════════════════

def setup_logging(verbose: bool, log_file: Path) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


logger = logging.getLogger("econtract")


# ═══════════════════════════════════════════════════════════════════════════
#  Banner
# ═══════════════════════════════════════════════════════════════════════════

BANNER = r"""
╔══════════════════════════════════════════════════════════════╗
║      eContract → Smart Contract Converter  v2.0              ║
║      Solidity 0.8.16  |  Local LLM (Ollama)                  ║
╚══════════════════════════════════════════════════════════════╝
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Core pipeline
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline_for_file(input_file: Path, args: argparse.Namespace) -> int:
    """
    Full conversion pipeline for a single file.
    Exit codes:
      0 — success, all validations passed
      1 — hard failure (extraction / LLM error)
      2 — generated but with structural warnings
      3 — generated but failed legal/compliance quality gate
    """
    t0 = time.monotonic()

    input_path  = input_file.resolve()
    # Generate a unique output directory for each file based on its name
    output_dir  = Path(args.output).resolve() / input_path.stem
    log_file    = output_dir / "logs" / "conversion.log"

    # Reset logging for each file
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    setup_logging(args.verbose, log_file)
    logger = logging.getLogger(f"econtract.{input_path.stem}")

    logger.info("━" * 60)
    logger.info(f"Processing file: {input_path}")

    # ── Step 1: Validate input ────────────────────────────────────────────
    logger.info(f"Input file  : {input_path}")
    logger.info(f"Output dir  : {output_dir}")
    logger.info(f"LLM model   : {args.model}")
    logger.info(f"Backend     : {args.backend}")

    if not input_path.exists():
        logger.error(f"File not found: {input_path}")
        return 1
    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.error(
            f"Unsupported file type '{input_path.suffix}'. "
            f"Accepted: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
        return 1

    # ── Step 2: Extract contract ──────────────────────────────────────────
    logger.info("━" * 60)
    logger.info("STEP 1/5  Extracting & preprocessing contract...")
    try:
        doc = extract_contract(input_path)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return 1

    logger.info(f"  Title      : {doc.title}")
    logger.info(f"  Parties    : {len(doc.parties)}")
    logger.info(f"  Clauses    : {len(doc.clauses)}")
    logger.info(f"  Characters : {doc.metadata.get('char_count', 0):,}")
    for p in doc.parties:
        w = f" ({p.wallet_hint})" if p.wallet_hint else ""
        logger.info(f"    - {p.role}: {p.name}{w}")

    # ── Step 3: Build prompts ─────────────────────────────────────────────
    logger.info("━" * 60)
    logger.info("STEP 2/5  Building LLM prompts...")
    system_prompt = get_system_prompt()
    user_prompt   = build_user_prompt(doc)

    if args.verbose:
        logger.debug("── USER PROMPT ──────────────────────────────────────")
        for line in user_prompt.splitlines()[:40]:
            logger.debug(f"  {line}")
        logger.debug("  ...")

    if args.dry_run:
        logger.info("DRY RUN: skipping LLM call. Printing prompt excerpt.")
        print("\n" + user_prompt[:2000])
        return 0

    # ── Step 4: LLM generation ────────────────────────────────────────────
    logger.info("━" * 60)
    logger.info("STEP 3/5  Generating smart contract with LLM...")

    cfg = LLMConfig(
        model=args.model,
        base_url=args.ollama_url,
        backend=args.backend,
        temperature=args.temperature,
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    client = LLMClient(cfg)

    if not client.health_check():
        logger.error(
            f"Cannot reach LLM backend at {args.ollama_url}. "
            "Make sure Ollama is running: `ollama serve`"
        )
        return 1

    if args.backend == "ollama":
        try:
            client.ensure_model()
        except RuntimeError as e:
            logger.warning(str(e))

    try:
        raw_code, issues = client.generate_contract(system_prompt, user_prompt, validate_pass=True)
    except RuntimeError as e:
        logger.error(f"LLM generation failed: {e}")
        return 1

    logger.info(f"  Raw output : {len(raw_code):,} characters")

    # ── Optional second-pass LLM validation ──────────────────────────────
    if args.validate_llm and issues:
        logger.info("  Running LLM self-validation pass...")
        val_prompt = build_validation_prompt(raw_code, doc)
        try:
            reviewed, issues2 = client.generate_contract(
                "You are a Solidity security auditor. Output only corrected code.",
                val_prompt,
                validate_pass=True,
            )
            if len(reviewed) > 200:
                raw_code = reviewed
                issues   = issues2
                logger.info("  LLM validation pass complete.")
        except Exception as e:
            logger.warning(f"  Validation pass failed (non-fatal): {e}")

    # ── Step 5: Post-process ──────────────────────────────────────────────
    logger.info("━" * 60)
    logger.info("STEP 4/5  Post-processing & applying Solidity fixes...")

    final_code = apply_all_fixes(raw_code, doc)

    # Re-run structural checks after deterministic fixes
    ok, final_issues = validate_solidity_output(final_code)

    elapsed = time.monotonic() - t0

    # ── Step 6: Legal & compliance validation ─────────────────────────────
    validation_report = None
    if not args.skip_validation:
        logger.info("━" * 60)
        logger.info("STEP 5/5  Running smart contract legal validation suite...")
        validation_report = run_contract_validation(final_code, doc)

        if validation_report:
            logger.info(f"  Overall accuracy   : {validation_report.accuracy_overall:.1f}%")
            logger.info(f"  Solidity standards : {validation_report.accuracy_solidity:.1f}%")
            logger.info(f"  Security           : {validation_report.accuracy_security:.1f}%")
            logger.info(f"  Legal faithfulness : {validation_report.accuracy_legal:.1f}%")
            logger.info(f"  Clause coverage    : {validation_report.accuracy_coverage:.1f}%")
            logger.info(f"  Tests              : {validation_report.passed}/{validation_report.total_tests} passed")

            if validation_report.critical_failures:
                logger.warning(
                    f"  ⚠  {validation_report.critical_failures} CRITICAL validation failure(s)!"
                )
                for r in validation_report.results:
                    if not r.passed and r.severity == "critical":
                        logger.warning(f"     • [{r.test_id}] {r.description}: {r.detail}")
            else:
                logger.info("  ✓ No critical validation failures.")
        else:
            logger.warning("  Validator not available — skipping.")
    else:
        logger.info("STEP 5/5  Legal validation skipped (--skip-validation).")

    # ── Save outputs ──────────────────────────────────────────────────────
    sol_path = save_solidity(final_code, doc, output_dir)
    rep_path = save_report(
        doc, sol_path, final_issues, output_dir, elapsed,
        validation_report=validation_report,
    )
    sum_path = save_human_readable_summary(
        doc, sol_path, output_dir,
        validation_report=validation_report,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    logger.info("━" * 60)
    logger.info(f"CONVERSION COMPLETE for {input_path.name}")
    logger.info(f"  Solidity file : {sol_path}")
    logger.info(f"  Report        : {rep_path}")
    logger.info(f"  Summary       : {sum_path}")
    logger.info(f"  Elapsed       : {elapsed:.1f}s")

    if final_issues:
        logger.warning(f"  Structural issues ({len(final_issues)}):")
        for issue in final_issues:
            logger.warning(f"     • {issue}")
    else:
        logger.info("  All structural validations passed.")

    if validation_report:
        logger.info(f"  Accuracy score : {validation_report.accuracy_overall:.1f}%")

    logger.info("━" * 60)

    if args.print_code:
        print("\n" + "═" * 70)
        print(f"GENERATED SOLIDITY FOR: {input_path.name}")
        print("═" * 70)
        print(final_code)

    # Exit code reflects worst issue found
    if validation_report and validation_report.critical_failures > 0:
        return 3   # legal/compliance critical failures
    if not ok:
        return 2   # structural warnings only
    return 0


def run_pipeline(args: argparse.Namespace) -> int:
    """
    Main entry point. Handles multiple files.
    """
    print(BANNER)
    overall_exit_code = 0

    for input_file_str in args.inputs:
        input_file = Path(input_file_str)
        exit_code = run_pipeline_for_file(input_file, args)
        if exit_code > overall_exit_code:
            overall_exit_code = exit_code

    num_files = len(args.inputs)
    logger.info(f"Processed {num_files} file(s).")
    if overall_exit_code > 0:
        logger.warning("One or more files had issues during conversion.")
    else:
        logger.info("All files converted successfully.")

    return overall_exit_code


# ═══════════════════════════════════════════════════════════════════════════
#  CLI argument parser
# ═══════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="econtract_converter",
        description="Convert eContracts (.docx/.txt) to Solidity 0.8.16 smart contracts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("inputs", nargs='+', help="Path(s) to the eContract file(s) (.docx or .txt)")
    p.add_argument("-o", "--output", default="./Results",
                   help="Output directory (default: ./Results)")
    p.add_argument("-m", "--model",
                   default=os.environ.get("LLM_MODEL", "qwen2.5-coder:14b"),
                   help="LLM model name")
    p.add_argument("--backend", choices=["ollama", "openai"],
                   default=os.environ.get("LLM_BACKEND", "ollama"))
    p.add_argument("--ollama-url",
                   default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    p.add_argument("--temperature", type=float, default=0.1)
    p.add_argument("--validate-llm", action="store_true",
                   help="Run a second LLM self-validation pass")
    p.add_argument("--skip-validation", action="store_true",
                   help="Skip the legal/compliance test suite")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract & build prompt only, do not call LLM")
    p.add_argument("--print-code", action="store_true",
                   help="Print the generated Solidity to stdout")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    # Setup initial logging to catch errors during arg parsing
    setup_logging(verbose=True, log_file=None)
    parser = build_parser()
    args   = parser.parse_args()
    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()