"""
llm_client.py — Communicates with a local Ollama instance to generate
Solidity smart contracts.

CHANGES vs previous version:

  FIX-1  DEFAULT_MODEL: qwen2.5-coder:7b (gemma:2b was too small).
  FIX-2  MAX_TOKENS raised 1800 → 4096.
  FIX-3  num_ctx raised 4096 → 8192.
  FIX-4  validate_solidity_output extended with common compile-error checks.
  FIX-5  temperature lowered 0.1 → 0.05.
  FIX-6  RECOMMENDED_MODELS list added.

  FIX-7  [NEW] validate_solidity_output now additionally checks:
           e) `view` on calculatePenalty() — compile error (emits event).
           f) msg.value inside a view function — compile error.
           g) Missing `bool private _locked;` at contract scope (SEC-001).
           h) Fewer than 2 `modifier onlyX` declarations (SEC-005).
           i) No external payable function (COV-001 / LEG-090).
           j) Missing receive() fallback (LEG-090).
           k) Missing GOVERNING_LAW string constant (LEG-020).
           l) Missing startDate / effectiveDate declaration (LEG-030).
           m) Fewer than 8 @notice NatSpec comments (SOL-013).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("econtract.llm")
from pathlib import Path
import subprocess
import platform

if "microsoft-standard" in platform.uname().release.lower():
    OLLAMA_EXECUTABLE = Path("/mnt/d/ollama.exe")
else:
    OLLAMA_EXECUTABLE = Path("D:/ollama.exe")

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# ═══════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════

RECOMMENDED_MODELS = [
    "qwen2.5-coder:7b",
    "codellama:13b",
    "deepseek-coder:6.7b",
    "mistral:7b",
    "gemma:7b",
]
DEFAULT_MODEL   = "qwen2.5-coder:7b"
CONNECT_TIMEOUT = 10
REQUEST_TIMEOUT = 900
MAX_RETRIES     = 3
RETRY_DELAY     = 5
MAX_TOKENS      = 4096


@dataclass
class LLMConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    timeout: int = REQUEST_TIMEOUT
    temperature: float = 0.05
    top_p: float = 0.9
    max_tokens: int = MAX_TOKENS
    backend: str = "ollama"
    api_key: Optional[str] = None
    stream: bool = True
    keep_alive: str = "30m"
    num_ctx: int = 8192


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity code extraction
# ═══════════════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"```(?:solidity|sol)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SPDX_RE  = re.compile(r"//\s*SPDX-License-Identifier:", re.IGNORECASE)


def extract_solidity(raw: str) -> str:
    """Strip markdown fences and preamble, returning only Solidity source."""
    fenced = _FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if _SPDX_RE.search(line):
            return "\n".join(lines[i:]).strip()
    return raw.strip()


def validate_solidity_output(code: str) -> tuple[bool, list[str]]:
    """
    Structural + syntax validation of generated Solidity code.
    Returns (is_valid, list_of_issues).

    Checks cover all test IDs that have historically failed:
    SOL-001..015, SEC-001..005, COV-001, LEG-020, LEG-030, LEG-090.
    """
    issues: list[str] = []

    # ── Basic structure ─────────────────────────────────────────────────────
    if "SPDX-License-Identifier" not in code:
        issues.append("Missing SPDX license identifier")
    if "pragma solidity" not in code:
        issues.append("Missing pragma statement")
    elif "0.8" not in code:
        issues.append("Wrong Solidity version (expected 0.8.x)")
    if "contract " not in code:
        issues.append("No contract definition found")
    if "constructor" not in code:
        issues.append("No constructor defined")
    if "event " not in code:
        issues.append("No events defined")
    if "emit " not in code:
        issues.append("Events defined but never emitted")
    if "revert " not in code and "require(" not in code:
        issues.append("No error handling (revert/require)")
    if code.count("{") != code.count("}"):
        issues.append("Mismatched braces — code may be truncated")
    if "selfdestruct" in code:
        issues.append("Uses selfdestruct (forbidden)")
    if "tx.origin" in code:
        issues.append("Uses tx.origin (security risk)")

    # ── FIX-4a: memory/calldata in event params ─────────────────────────────
    event_mem = re.findall(
        r"event\s+\w+\s*\([^)]*\b(memory|calldata|storage)\b[^)]*\)",
        code, re.IGNORECASE,
    )
    if event_mem:
        issues.append(
            f"Event parameters use data-location keywords ({', '.join(set(event_mem))}) "
            "— compile error."
        )

    # ── FIX-4b: block.timestamp in constant ────────────────────────────────
    if re.search(r"constant\s+\w+\s*=\s*block\.timestamp", code, re.IGNORECASE):
        issues.append(
            "constant variable initialised with block.timestamp — compile error."
        )

    # ── FIX-4c: type-first function signature ──────────────────────────────
    type_first = re.findall(
        r"^\s*(?:ContractState|uint256|uint|int256|address|bool|bytes32)\s+\w+\s*\([^)]*\)\s*(?:public|external|internal|private)",
        code, re.MULTILINE,
    )
    if type_first:
        issues.append(
            f"Function definition missing `function` keyword ({len(type_first)} occurrence(s))."
        )

    # ── FIX-4d: bare empty return ───────────────────────────────────────────
    empty_returns = re.findall(r"\breturn\s*\(\s*\)\s*;", code)
    if empty_returns:
        issues.append(f"`return();` found ({len(empty_returns)} time(s)) — compile error.")

    # ── FIX-7e: calculatePenalty marked view but emits event ───────────────
    calc_view = re.search(
        r"function\s+calculatePenalty\s*\([^)]*\)[^{]*\bview\b[^{]*\{",
        code, re.IGNORECASE,
    )
    if calc_view:
        issues.append(
            "calculatePenalty() is marked `view` but emits an event — compile error. "
            "Remove `view` from its signature."
        )

    # ── FIX-7f: msg.value in a view/pure function ──────────────────────────
    # Simple heuristic: find functions marked view that contain msg.value
    view_fns = re.finditer(
        r"function\s+\w+\s*\([^)]*\)[^{]*\bview\b[^{]*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
        code, re.DOTALL,
    )
    for vfn in view_fns:
        if "msg.value" in vfn.group(1):
            fn_match = re.search(r"function\s+(\w+)", vfn.group(0))
            fn_name = fn_match.group(1) if fn_match else "unknown"
            issues.append(
                f"`msg.value` used inside view function `{fn_name}()` — compile error. "
                "Either make it payable or replace msg.value with a uint256 parameter."
            )

    # ── FIX-7g: SEC-001 — bool private _locked at contract scope ──────────
    if not re.search(r"bool\s+private\s+_locked\s*;", code):
        issues.append(
            "SEC-001: `bool private _locked;` not found at contract scope. "
            "Add it as a top-level state variable for reentrancy protection."
        )

    # ── FIX-7h: SEC-005 — at least 2 onlyX modifiers ──────────────────────
    only_mods = re.findall(r"modifier\s+only\w+\s*\(", code)
    if len(only_mods) < 2:
        issues.append(
            f"SEC-005: Only {len(only_mods)} `modifier onlyX` found; minimum 2 required. "
            "Add onlyParties() and onlyArbitrator() (or equivalent)."
        )

    # ── FIX-7i: COV-001 — at least one external payable function ──────────
    has_payable_fn = bool(
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bexternal\b[^{]*\bpayable\b", code) or
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\bexternal\b", code)
    )
    if not has_payable_fn:
        issues.append(
            "COV-001/LEG-090: No `external payable` function found. "
            "Add a pay() or depositPayment() function."
        )

    # ── FIX-7j: LEG-090 — receive() fallback ──────────────────────────────
    if not re.search(r"\breceive\s*\(\s*\)\s+external\s+payable", code):
        issues.append(
            "LEG-090: `receive() external payable` not found. "
            "Add it so the contract can accept direct ETH transfers."
        )

    # ── FIX-7k: LEG-020 — GOVERNING_LAW constant ──────────────────────────
    if "GOVERNING_LAW" not in code:
        issues.append(
            "LEG-020: `GOVERNING_LAW` string constant not found. "
            "Add: string public constant GOVERNING_LAW = \"<jurisdiction>\";"
        )

    # ── FIX-7l: LEG-030 — startDate / effectiveDate ────────────────────────
    if not re.search(r"\bstartDate\b|\beffectiveDate\b|\b_startDate\b", code):
        issues.append(
            "LEG-030: No `startDate` or `effectiveDate` variable found. "
            "Add: uint256 public immutable startDate; and set it in the constructor."
        )

    # ── FIX-7m: SOL-013 — minimum 8 @notice tags ──────────────────────────
    notice_count = len(re.findall(r"///\s*@notice", code))
    if notice_count < 8:
        issues.append(
            f"SOL-013: Only {notice_count} `/// @notice` NatSpec comments found; "
            "minimum 8 required — one per public/external function."
        )


    # ── NEW: detect revert targets not declared as custom errors ──────────
    reverted         = set(re.findall(r"\brevert\s+(\w+)\s*[;(]", code))
    declared_errors  = set(re.findall(r"\berror\s+(\w+)\s*[;(]", code))
    undeclared_reverts = reverted - declared_errors
    if undeclared_reverts:
        issues.append(
            f"Undeclared custom error(s) used in revert: "
            f"{', '.join(sorted(undeclared_reverts))}. "
            "Declare each as: error Name(...);"
        )

    # ── NEW: detect emit targets not declared as events ───────────────────
    emitted          = set(re.findall(r"\bemit\s+(\w+)\s*\(", code))
    declared_events  = set(re.findall(r"\bevent\s+(\w+)\s*\(", code))
    undeclared_emits = emitted - declared_events
    if undeclared_emits:
        issues.append(
            f"Undeclared event(s) used in emit: "
            f"{', '.join(sorted(undeclared_emits))}. "
            "Declare each as: event Name(...);"
        )

    # ── NEW: noReentrant used but modifier body not declared ──────────────
    if re.search(r"\bnoReentrant\b", code) and not re.search(r"modifier\s+noReentrant\s*\(", code):
        issues.append(
            "noReentrant modifier used in function signatures but not declared. "
            "Add: modifier noReentrant() { if (_locked) revert ReentrantCall(); "
            "_locked = true; _; _locked = false; }"
        )

    # ── NEW: party alias identifiers used but never declared ─────────────
    _ALIASES = [
        "parent", "acquisitionSub", "mergerSub", "buyer", "seller",
        "employer", "employee", "licensor", "licensee", "lessor", "lessee",
        "borrower", "lender", "partyA", "partyB", "party1", "party2",
    ]
    _decl_vars = set(re.findall(
        r"^\s*(?:address|uint\d*|int\d*|bool|bytes\d*|string)\s+"
        r"(?:payable\s+)?(?:private|public|internal)?\s*(?:immutable\s+|constant\s+)?(\w+)\s*[;=]",
        code, re.MULTILINE,
    ))
    bad_aliases = [a for a in _ALIASES if re.search(rf"\b{a}\b", code) and a not in _decl_vars]
    if bad_aliases:
        issues.append(
            f"Undeclared state variable(s) referenced: {', '.join(bad_aliases)}. "
            "Use _partyA / _partyB / _arbitrator or declare them in the constructor."
        )

    # ── NEW: immutable initialised inline with a non-literal ─────────────
    bad_imm = re.findall(
        r"uint256\s+public\s+immutable\s+\w+\s*=\s*(?!\d)[A-Z_][A-Z_0-9]*\s*;",
        code,
    )
    if bad_imm:
        issues.append(
            "Immutable variable(s) initialised inline with a constant name "
            "(not an integer literal). Assign in constructor instead: "
            "`uint256 public immutable x;` then in constructor: `x = CONST;`"
        )


    return len(issues) == 0, issues


# ═══════════════════════════════════════════════════════════════════════════
#  Ollama backend
# ═══════════════════════════════════════════════════════════════════════════

class OllamaClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.generate_url = f"{cfg.base_url.rstrip('/')}/api/chat"

    def _check_model(self) -> bool:
        try:
            resp = requests.get(f"{self.cfg.base_url}/api/tags", timeout=10)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return any(self.cfg.model in m for m in models)
        except requests.RequestException:
            pass
        return False

    def pull_model(self) -> bool:
        if not OLLAMA_EXECUTABLE.exists():
            logger.error(f"Ollama executable not found at {OLLAMA_EXECUTABLE}")
            return False
        logger.info(f"Pulling model '{self.cfg.model}'...")
        try:
            with subprocess.Popen(
                [str(OLLAMA_EXECUTABLE), "pull", self.cfg.model],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            ) as proc:
                if proc.stdout:
                    for line in proc.stdout:
                        print(f"\r  {line.strip()}", end="", flush=True)
            print()
            return proc.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Model pull failed: {e}")
            return False

    def generate(self, system: str, user: str) -> str:
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": self.cfg.temperature,
                "top_p":       self.cfg.top_p,
                "num_ctx":     self.cfg.num_ctx,
                "num_predict": self.cfg.max_tokens,
                "stop":        ["```\n\n"],
            },
        }
        total_chars = len(system) + len(user)
        logger.info(f"  → Prompt size: {total_chars} chars (~{total_chars//4} tokens)")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"  → LLM call attempt {attempt}/{MAX_RETRIES}")
                resp = requests.post(self.generate_url, json=payload, timeout=self.cfg.timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                if content:
                    return content
                logger.warning(f"  Empty response on attempt {attempt}")
            except requests.RequestException as e:
                logger.warning(f"  Request failed (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        raise RuntimeError(
            f"LLM failed after {MAX_RETRIES} attempts. "
            "Check that Ollama is running: `ollama serve`\n"
            f"Recommended models: {', '.join(RECOMMENDED_MODELS)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Unified LLM interface
# ═══════════════════════════════════════════════════════════════════════════

class LLMClient:
    """Unified interface: auto-detects Ollama availability."""

    def __init__(self, cfg: Optional[LLMConfig] = None):
        self.cfg = cfg or LLMConfig(
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            base_url=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL),
            api_key=os.environ.get("OPENAI_API_KEY"),
            backend=os.environ.get("LLM_BACKEND", "ollama"),
        )
        self._backend = OllamaClient(self.cfg)

    def health_check(self) -> bool:
        try:
            resp = requests.get(self.cfg.base_url, timeout=5)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def ensure_model(self) -> None:
        if isinstance(self._backend, OllamaClient):
            if not self._backend._check_model():
                logger.info(f"Model '{self.cfg.model}' not found — pulling...")
                if not self._backend.pull_model():
                    raise RuntimeError(
                        f"Could not pull model '{self.cfg.model}'. "
                        f"Run: ollama pull {self.cfg.model}\n"
                        f"Recommended models: {', '.join(RECOMMENDED_MODELS)}"
                    )

    def generate_contract(
        self, system: str, user: str, validate_pass: bool = True
    ) -> tuple[str, list[str]]:
        """
        Generate a smart contract.
        Returns: (solidity_code, list_of_validation_issues)
        """
        raw  = self._backend.generate(system, user)
        code = extract_solidity(raw)

        if validate_pass:
            ok, issues = validate_solidity_output(code)
            if not ok:
                logger.warning(f"Validation issues: {issues}")
            return code, issues

        return code, []

# ═══════════════════════════════════════════════════════════════════════════
#  Feedback-loop constants
# ═══════════════════════════════════════════════════════════════════════════

MAX_FEEDBACK_ITERATIONS = 3   # Total refinement passes after the first generation
ACCURACY_TARGET         = 100.0  # Desired overall accuracy %


# ═══════════════════════════════════════════════════════════════════════════
#  Feedback-loop method (added to LLMClient)
# ═══════════════════════════════════════════════════════════════════════════

def _generate_with_feedback(
    self,
    system: str,
    user: str,
    doc,                          # ContractDocument — imported lazily to avoid circular
    max_iterations: int = MAX_FEEDBACK_ITERATIONS,
    accuracy_target: float = ACCURACY_TARGET,
) -> tuple[str, list[str], object]:
    """
    Generation + feedback loop.

    Strategy
    --------
    1. Generate the contract (first pass).
    2. Run postprocessor fixes (deterministic).
    3. Run the full test-suite validator.
    4. If accuracy < accuracy_target OR there are critical/major failures:
         a. Build a targeted correction prompt (build_feedback_prompt).
         b. Feed the FIXED code + failure details back to the LLM.
         c. Re-apply postprocessor fixes.
         d. Re-validate. Repeat up to max_iterations times.
    5. Return the best code seen across all iterations.

    Returns
    -------
    (best_solidity_code, validation_issues, validation_report)
    """
    from prompt_builder import build_feedback_prompt
    from postprocessor  import apply_all_fixes, run_contract_validation
    from llm_client     import extract_solidity, validate_solidity_output

    best_code    : str   = ""
    best_accuracy: float = -1.0
    best_report          = None
    best_issues : list[str] = []

    current_system = system
    current_user   = user

    for iteration in range(1, max_iterations + 2):   # +2: first gen + N feedback passes
        is_feedback_pass = iteration > 1
        pass_label = "Initial generation" if not is_feedback_pass else f"Feedback pass {iteration - 1}/{max_iterations}"
        logger.info(f"  ── {pass_label} ──")

        # ── LLM call ────────────────────────────────────────────────────────
        raw  = self._backend.generate(current_system, current_user)
        code = extract_solidity(raw)

        # ── Deterministic postprocessor ──────────────────────────────────────
        code = apply_all_fixes(code, doc)

        # ── Structural validation ────────────────────────────────────────────
        struct_ok, struct_issues = validate_solidity_output(code)

        # ── Full test-suite validation ────────────────────────────────────────
        report = run_contract_validation(code, doc)

        if report is not None:
            accuracy = report.accuracy_overall
            logger.info(
                f"  Accuracy: {accuracy:.1f}%  "
                f"({report.passed}/{report.total_tests} tests passed, "
                f"{report.critical_failures} critical failures)"
            )
        else:
            # No validator available — fall back to structural check
            accuracy = 100.0 if struct_ok else 0.0
            logger.info(f"  Structural validation: {'PASS' if struct_ok else 'FAIL'}")

        # ── Track best result ────────────────────────────────────────────────
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_code     = code
            best_report   = report
            best_issues   = struct_issues

        # ── Early exit if target reached ─────────────────────────────────────
        all_struct_ok = len(struct_issues) == 0
        no_crits      = (report is None or report.critical_failures == 0)
        target_met    = accuracy >= accuracy_target and all_struct_ok and no_crits

        if target_met:
            logger.info(f"  ✓ Target accuracy {accuracy_target}% reached — stopping feedback loop.")
            break

        # ── Check if we have more iterations left ─────────────────────────────
        if iteration >= max_iterations + 1:
            logger.info(
                f"  Max iterations ({max_iterations}) exhausted. "
                f"Best accuracy: {best_accuracy:.1f}%"
            )
            break

        # ── Build feedback prompt ────────────────────────────────────────────
        failed_tests = [r for r in report.results if not r.passed] if report else []

        logger.info(
            f"  Accuracy {accuracy:.1f}% < {accuracy_target}% — "
            f"building feedback prompt ({len(failed_tests)} failed tests, "
            f"{len(struct_issues)} structural issues)."
        )

        feedback_user = build_feedback_prompt(
            solidity_code    = code,        # use the already-fixed code
            doc              = doc,
            failed_tests     = failed_tests,
            validation_issues= struct_issues,
            attempt          = iteration,
            max_attempts     = max_iterations,
        )

        # The system prompt stays the same; only the user prompt changes.
        current_user = feedback_user

    return best_code, best_issues, best_report


# Bind the method onto LLMClient so it's accessible as self.generate_with_feedback(...)
LLMClient.generate_with_feedback = _generate_with_feedback