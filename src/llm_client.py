"""
llm_client.py — Communicates with a local Ollama instance (or any
OpenAI-compatible endpoint) to generate Solidity smart contracts.

Supported backends:
  • Ollama  (default) — http://localhost:11434
  • OpenAI-compatible  — set OPENAI_BASE_URL env variable
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


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_OLLAMA_URL   = "http://localhost:11434"
DEFAULT_MODEL        = "qwen2.5-coder:7b"
REQUEST_TIMEOUT      = 300   # seconds — large contracts may take time
MAX_RETRIES          = 3
RETRY_DELAY          = 5     # seconds between retries
MAX_TOKENS           = 8192


@dataclass
class LLMConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    timeout: int = REQUEST_TIMEOUT
    temperature: float = 0.1       # low temp → deterministic, accurate output
    top_p: float = 0.9
    max_tokens: int = MAX_TOKENS
    backend: str = "ollama"        # "ollama" | "openai"
    api_key: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
#  Solidity code extraction from raw LLM output
# ═══════════════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"```(?:solidity|sol)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SPDX_RE  = re.compile(r"//\s*SPDX-License-Identifier:", re.IGNORECASE)


def extract_solidity(raw: str) -> str:
    """
    Strip markdown fences and any preamble/postamble from LLM output,
    returning only the Solidity source.
    """
    # Try to extract from fenced block first
    fenced = _FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()

    # Fall back: find the SPDX line and return everything from there
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if _SPDX_RE.search(line):
            return "\n".join(lines[i:]).strip()

    # Last resort: return as-is (cleaned)
    return raw.strip()


def validate_solidity_output(code: str) -> tuple[bool, list[str]]:
    """
    Basic structural validation of generated Solidity code.
    Returns (is_valid, list_of_issues).
    """
    issues: list[str] = []

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

    return len(issues) == 0, issues


# ═══════════════════════════════════════════════════════════════════════════
#  Ollama backend
# ═══════════════════════════════════════════════════════════════════════════

class OllamaClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.generate_url = f"{cfg.base_url.rstrip('/')}/api/chat"

    def _check_model(self) -> bool:
        """Verify the model is pulled and available."""
        try:
            resp = requests.get(
                f"{self.cfg.base_url}/api/tags", timeout=10
            )
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return any(self.cfg.model in m for m in models)
        except requests.RequestException:
            pass
        return False

    def pull_model(self) -> bool:
        """Pull the model if not available."""
        logger.info(f"Pulling model '{self.cfg.model}' from Ollama registry...")
        try:
            resp = requests.post(
                f"{self.cfg.base_url}/api/pull",
                json={"name": self.cfg.model, "stream": False},
                timeout=600,
            )
            return resp.status_code == 200
        except requests.RequestException as e:
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
                "num_predict": self.cfg.max_tokens,
                "stop":        ["```\n\n"],   # stop after code block ends
            },
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"  → LLM call attempt {attempt}/{MAX_RETRIES} (model: {self.cfg.model})")
                resp = requests.post(
                    self.generate_url,
                    json=payload,
                    timeout=self.cfg.timeout,
                )
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
            "Check that Ollama is running: `ollama serve`"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  OpenAI-compatible backend (fallback)
# ═══════════════════════════════════════════════════════════════════════════

class OpenAICompatClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.url = f"{cfg.base_url.rstrip('/')}/v1/chat/completions"

    def generate(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    self.url, json=payload, headers=headers, timeout=self.cfg.timeout
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except (requests.RequestException, KeyError, IndexError) as e:
                logger.warning(f"Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        raise RuntimeError("OpenAI-compatible API failed after retries.")


# ═══════════════════════════════════════════════════════════════════════════
#  Unified LLM interface
# ═══════════════════════════════════════════════════════════════════════════

class LLMClient:
    """
    Unified interface: auto-detects Ollama availability, falls back to
    OpenAI-compatible if configured.
    """

    def __init__(self, cfg: Optional[LLMConfig] = None):
        self.cfg = cfg or LLMConfig(
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            base_url=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL),
            api_key=os.environ.get("OPENAI_API_KEY"),
            backend=os.environ.get("LLM_BACKEND", "ollama"),
        )

        if self.cfg.backend == "ollama":
            self._backend = OllamaClient(self.cfg)
        else:
            self._backend = OpenAICompatClient(self.cfg)

    def health_check(self) -> bool:
        """Returns True if the LLM backend is reachable."""
        try:
            resp = requests.get(self.cfg.base_url, timeout=5)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def ensure_model(self) -> None:
        """Pull model if using Ollama and model is not yet available."""
        if isinstance(self._backend, OllamaClient):
            if not self._backend._check_model():
                logger.info(f"Model '{self.cfg.model}' not found locally — pulling...")
                if not self._backend.pull_model():
                    raise RuntimeError(
                        f"Could not pull model '{self.cfg.model}'. "
                        "Run manually: ollama pull qwen2.5-coder:7b"
                    )

    def generate_contract(
        self, system: str, user: str, validate_pass: bool = True
    ) -> tuple[str, list[str]]:
        """
        Generate a smart contract.

        Args:
            system:        System prompt.
            user:          User prompt with contract details.
            validate_pass: Whether to run structural validation.

        Returns:
            (solidity_code, list_of_validation_issues)
        """
        raw = self._backend.generate(system, user)
        code = extract_solidity(raw)

        if validate_pass:
            ok, issues = validate_solidity_output(code)
            if not ok:
                logger.warning(f"Validation issues: {issues}")
            return code, issues

        return code, []
