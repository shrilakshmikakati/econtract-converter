#!/usr/bin/env python3
"""
test_pipeline.py — Unit + integration tests for the eContract converter.
Runs WITHOUT an LLM (uses mock or dry-run mode).

Usage:
    python tests/test_pipeline.py
"""

import sys
import json
import time
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from extractor import extract_contract, _clean_text, _split_into_clauses, ContractDocument
from prompt_builder import build_user_prompt, get_system_prompt
from postprocessor import apply_all_fixes, _fix_pragma, _fix_spdx, _fix_safemath, _slugify


SAMPLE_TXT = Path(__file__).parent / "sample_service_agreement.txt"


class TestTextCleaning(unittest.TestCase):
    def test_noise_removal(self):
        raw = "CONFIDENTIAL - DO NOT DISTRIBUTE\nReal content here\nPage 1 of 3"
        clean = _clean_text(raw)
        self.assertNotIn("DO NOT DISTRIBUTE", clean)
        self.assertNotIn("Page 1 of 3", clean)
        self.assertIn("Real content here", clean)

    def test_unicode_normalization(self):
        raw = "Party \u201cAlpha\u201d shall pay \u20181000\u2019 ETH."
        clean = _clean_text(raw)
        self.assertIn('"Alpha"', clean)
        self.assertIn("'1000'", clean)

    def test_multiple_blank_lines(self):
        raw = "Line 1\n\n\n\n\nLine 2"
        clean = _clean_text(raw)
        self.assertNotIn("\n\n\n", clean)


class TestExtraction(unittest.TestCase):
    def setUp(self):
        if not SAMPLE_TXT.exists():
            self.skipTest("Sample file not found")
        self.doc = extract_contract(SAMPLE_TXT)

    def test_returns_contract_document(self):
        self.assertIsInstance(self.doc, ContractDocument)

    def test_title_extracted(self):
        self.assertTrue(len(self.doc.title) > 3)
        print(f"\n  Title: {self.doc.title}")

    def test_parties_extracted(self):
        self.assertGreaterEqual(len(self.doc.parties), 1)
        for p in self.doc.parties:
            self.assertTrue(p.role)
            self.assertTrue(p.name)
        print(f"\n  Parties: {[(p.role, p.name) for p in self.doc.parties]}")

    def test_clauses_extracted(self):
        self.assertGreaterEqual(len(self.doc.clauses), 3)
        print(f"\n  Clauses: {[(c.index, c.heading, c.clause_type) for c in self.doc.clauses]}")

    def test_eth_addresses_found(self):
        addrs = self.doc.metadata.get("eth_addresses_found", [])
        self.assertGreaterEqual(len(addrs), 1)
        print(f"\n  ETH addresses: {addrs}")

    def test_payment_clause_amount(self):
        payment_clauses = [c for c in self.doc.clauses if c.clause_type == "payment"]
        print(f"\n  Payment clauses: {len(payment_clauses)}")
        for c in payment_clauses:
            print(f"    - {c.heading}: amount={c.amount_eth}, days={c.deadline_days}")

    def test_metadata(self):
        self.assertIn("source_file", self.doc.metadata)
        self.assertIn("clause_count", self.doc.metadata)
        self.assertGreater(self.doc.metadata["char_count"], 100)

    def test_unsupported_extension(self):
        with self.assertRaises(ValueError):
            extract_contract("contract.pdf")

    def test_nonexistent_file(self):
        with self.assertRaises(ValueError):
            extract_contract("/tmp/does_not_exist_xyz.txt")


class TestPromptBuilder(unittest.TestCase):
    def setUp(self):
        if not SAMPLE_TXT.exists():
            self.skipTest("Sample file not found")
        self.doc = extract_contract(SAMPLE_TXT)

    def test_system_prompt_contains_solidity_rules(self):
        sp = get_system_prompt()
        self.assertIn("0.8.16", sp)
        self.assertIn("SPDX", sp)
        self.assertIn("custom errors", sp.lower())

    def test_user_prompt_contains_title(self):
        up = build_user_prompt(self.doc)
        self.assertIn(self.doc.title, up)

    def test_user_prompt_contains_parties(self):
        up = build_user_prompt(self.doc)
        for p in self.doc.parties:
            self.assertIn(p.role, up)

    def test_user_prompt_contains_clause_types(self):
        up = build_user_prompt(self.doc)
        for c in self.doc.clauses:
            self.assertIn(c.clause_type.upper(), up)

    def test_prompt_not_empty(self):
        up = build_user_prompt(self.doc)
        self.assertGreater(len(up), 200)


class TestPostprocessor(unittest.TestCase):
    SAMPLE_SOL = """
contract TestContract {
    constructor() {}
}
"""

    def test_fix_spdx(self):
        fixed = _fix_spdx(self.SAMPLE_SOL)
        self.assertIn("SPDX-License-Identifier", fixed)

    def test_fix_pragma(self):
        code = "pragma solidity ^0.7.0;\n" + self.SAMPLE_SOL
        fixed = _fix_pragma(code)
        self.assertIn("0.8.16", fixed)
        self.assertNotIn("0.7.0", fixed)

    def test_fix_safemath(self):
        code = "import 'SafeMath.sol';\nusing SafeMath for uint256;\n" + self.SAMPLE_SOL
        fixed = _fix_safemath(code)
        self.assertNotIn("SafeMath", fixed)

    def test_slugify(self):
        self.assertEqual(_slugify("Service Agreement Contract"), "service_agreement_contract")
        self.assertIn("nda", _slugify("NDA & IP Rights!"))
        self.assertIn("ip", _slugify("NDA & IP Rights!"))

    def test_full_fixes(self):
        if not SAMPLE_TXT.exists():
            self.skipTest("Sample file not found")
        doc = extract_contract(SAMPLE_TXT)
        raw = "pragma solidity ^0.7.6;\ncontract X { constructor() payable {} }\n"
        fixed = apply_all_fixes(raw, doc)
        self.assertIn("0.8.16", fixed)
        self.assertIn("SPDX-License-Identifier", fixed)
        self.assertIn("receive()", fixed)
        self.assertIn("Generated:", fixed)


class TestEndToEnd(unittest.TestCase):
    """Dry-run integration test — no LLM required."""

    def test_dry_run_pipeline(self):
        """Run the full pipeline in dry-run mode and verify the prompt is built."""
        if not SAMPLE_TXT.exists():
            self.skipTest("Sample file not found")

        doc = extract_contract(SAMPLE_TXT)
        system = get_system_prompt()
        user   = build_user_prompt(doc)

        self.assertGreater(len(system), 100)
        self.assertGreater(len(user), 100)
        self.assertIn("INSTRUCTIONS", user)

        print(f"\n  Dry-run prompt length: {len(user):,} chars")
        print(f"  System prompt length : {len(system):,} chars")
        print(f"  Clauses extracted    : {len(doc.clauses)}")
        print(f"  Parties extracted    : {len(doc.parties)}")


if __name__ == "__main__":
    print("=" * 60)
    print("  eContract Converter — Test Suite")
    print("=" * 60)
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
