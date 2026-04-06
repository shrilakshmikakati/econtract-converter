#!/usr/bin/env python3
"""
econtract_converter.py — Production-ready CLI tool
Converts electronic contracts (.docx / .txt) → Solidity 0.8.16 smart contracts
using a local LLM (Ollama + qwen2.5-coder:7b by default).

Results folder contains ONLY:
  <contract_name>.sol   — the generated Solidity smart contract
  results.json          — full metadata + accuracy scores + per-test results

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
import shutil
import sys
import time
from pathlib import Path

# ── Local modules ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))

from extractor import extract_contract, SUPPORTED_EXTENSIONS
from prompt_builder import build_user_prompt, get_system_prompt, build_validation_prompt
from llm_client import LLMClient, LLMConfig, validate_solidity_output
from postprocessor import apply_all_fixes, save_solidity, save_report, save_human_readable_summary


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
╔══════════════════════════════════════════════════════════════╗
║      eContract → Smart Contract Converter  v1.0              ║
║      Solidity 0.8.16  |  Local LLM (Ollama)                  ║
╚══════════════════════════════════════════════════════════════╝
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Core pipeline
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline_for_file(input_file: Path, args: argparse.Namespace) -> int:
    """
    Full conversion pipeline.
    Returns exit code (0 = success, 1 = failure).
    """
    print(BANNER)
    t0 = time.monotonic()

    input_path = input_file.resolve()
    output_dir = Path(args.output).resolve() / input_path.stem

    # Reset logging for each file
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    setup_logging(args.verbose)
    logger = logging.getLogger(f"econtract.{input_path.stem}")

    logger.info("━" * 60)
    logger.info(f"Processing file: {input_path}")
    logger.info(f"Output dir     : {output_dir}")
    logger.info(f"LLM model      : {args.model}")
    logger.info(f"Backend        : {args.backend}")

    # ── Step 1: Validate input ────────────────────────────────────────────
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

    # Re-validate after fixes
    ok, final_issues = validate_solidity_output(final_code)

    elapsed = time.monotonic() - t0

    sol_path = save_solidity(final_code, doc, output_dir)
    rep_path = save_report(doc, sol_path, final_issues, output_dir, elapsed)
    sum_path = save_human_readable_summary(doc, sol_path, output_dir)

    # ── Summary ───────────────────────────────────────────────────────────
    logger.info("━" * 60)
    logger.info(f"CONVERSION COMPLETE  →  {input_path.name}")
    logger.info(f"   Solidity  : {sol_path.name}")
    logger.info(f"   Report    : results.json")
    logger.info(f"   Elapsed  : {elapsed:.1f}s")

    if final_issues:
        logger.warning(f"  Structural issues ({len(final_issues)}):")
        for issue in final_issues:
            logger.warning(f"     • {issue}")
    else:
        logger.info("  ✓  All structural validations passed.")

    logger.info("━" * 60)

    if args.print_code:
        print("\n" + "═" * 70)
        print(f"GENERATED SOLIDITY — {input_path.name}")
        print("═" * 70)
        print(final_code)

    return 0 if ok else 2   # exit 2 = generated but with warnings


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

    p.add_argument(
        "input",
        help="Path to the eContract file (.docx or .txt)",
    )
    p.add_argument(
        "-o", "--output",
        default="./output",
        help="Output directory (default: ./output)",
    )
    p.add_argument(
        "-m", "--model",
        default=os.environ.get("LLM_MODEL", "qwen2.5-coder:7b"),
        help="LLM model name (default: qwen2.5-coder:7b)",
    )
    p.add_argument(
        "--backend",
        choices=["ollama", "openai"],
        default=os.environ.get("LLM_BACKEND", "ollama"),
        help="LLM backend (default: ollama)",
    )
    p.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="Ollama base URL (default: http://localhost:11434)",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM temperature 0–1 (default: 0.1 for accuracy)",
    )
    p.add_argument(
        "--validate-llm",
        action="store_true",
        help="Run a second LLM pass to self-validate the generated contract",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract & build prompt only, do not call LLM",
    )
    p.add_argument(
        "--print-code",
        action="store_true",
        help="Print the generated Solidity to stdout",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    return p


def main() -> None:
    setup_logging(verbose=True)
    parser = build_parser()
    args   = parser.parse_args()
    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()