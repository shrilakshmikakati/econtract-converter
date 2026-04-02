# eContract → Smart Contract Converter

**Production-ready** tool that converts electronic contracts (`.docx` / `.txt`) into
**Solidity 0.8.16** smart contracts using a **local LLM** (Ollama + `qwen2.5-coder:7b`).

---

## Architecture

```
eContract File (.docx / .txt)
         │
         ▼
 ┌──────────────────┐
 │  extractor.py    │  ← Reads, cleans, preprocesses, extracts
 │                  │    parties, clauses, amounts, ETH addresses
 └────────┬─────────┘
          │ ContractDocument
          ▼
 ┌──────────────────┐
 │ prompt_builder.py│  ← Builds precision LLM prompt with
 │                  │    Solidity 0.8.16 rules + clause details
 └────────┬─────────┘
          │ system + user prompt
          ▼
 ┌──────────────────┐
 │  llm_client.py   │  ← Sends to Ollama (qwen2.5-coder:7b)
 │                  │    Retries, extracts code from response
 └────────┬─────────┘
          │ raw Solidity
          ▼
 ┌──────────────────┐
 │ postprocessor.py │  ← Deterministic fixes: pragma, SPDX,
 │                  │    SafeMath removal, receive(), banner
 └────────┬─────────┘
          │
          ▼
   output/
   ├── contract.sol        ← Solidity 0.8.16 smart contract
   ├── contract_report.json← Conversion metadata + validation
   └── contract_summary.md ← Human-readable clause mapping
```

---

## Quick Start

### 1. Install dependencies

```bash
# Clone / unzip the project
cd econtract_converter

# Install Python deps
./econtract.sh --install-deps

# Install Ollama (if not installed)
# Linux:
curl -fsSL https://ollama.ai/install.sh | sh
# macOS:
brew install ollama

# Pull the model
./econtract.sh --pull-model
```

### 2. Start Ollama (if not running)

```bash
ollama serve
```

### 3. Convert a contract

```bash
./econtract.sh my_contract.docx
./econtract.sh my_contract.txt
./econtract.sh service_agreement.docx --output ./output --print-code
```

---

## Supported Input Formats

| Format | Notes |
|--------|-------|
| `.docx` | Microsoft Word documents. Tables extracted. |
| `.txt`  | UTF-8, Latin-1, CP1252 auto-detected. |

**Only `.docx` and `.txt` are accepted.** PDF, RTF, ODT are not supported.

---

## What Gets Extracted

| Field | How |
|-------|-----|
| Contract title | First all-caps or keyword-matched line |
| Parties & roles | Pattern matching: Buyer, Seller, Service Provider, Client, etc. |
| ETH addresses | `0x[40 hex chars]` regex |
| Clauses | Numbered/headed sections → typed as: `payment`, `penalty`, `expiry`, `obligation`, `dispute`, `confidential`, `ip` |
| Amounts | ETH / USD / wei amounts per clause |
| Deadlines | N-day windows per clause |
| Dates | Effective / expiry dates |
| Governing law | Regex pattern on "governed by the laws of…" |

### Preprocessing pipeline

1. Unicode NFC normalization
2. Smart quote → ASCII conversion
3. Noise removal: page numbers, "DRAFT", horizontal rules, HTML tags
4. Multiple blank lines collapsed
5. Tables flattened to `|`-separated rows
6. Clause segmentation by numbered headings or ALL CAPS headings
7. Per-clause classification, amount extraction, deadline extraction

---

## Generated Smart Contract Features

Every generated contract includes:

- `// SPDX-License-Identifier: MIT`
- `pragma solidity ^0.8.16;`
- **State machine** with `ContractState` enum
- **Custom errors** (gas-efficient, no require strings)
- **Events** for every state transition
- **Reentrancy guard** (`bool private locked`) on ETH-transfer functions
- **Payment functions** with exact wei validation per milestone
- **Penalty logic** computed in wei
- **Deadlines** as `block.timestamp + N * 1 days`
- **Dispute mechanism** with arbitrator address
- **Confidentiality acknowledgement** on-chain flag
- **IP transfer** event on full payment
- **`terminate()`** accessible by either party
- **`getContractState()`** view function
- **`receive()`** payable fallback
- **NatSpec** on every function and state variable
- No SafeMath (built-in 0.8.x overflow protection)
- No OpenZeppelin imports (standalone, zero dependencies)

---

## CLI Reference

```
./econtract.sh <input_file> [options]

Options:
  -o, --output DIR       Output directory (default: ./output)
  -m, --model MODEL      LLM model (default: qwen2.5-coder:7b)
  --backend BACKEND      ollama|openai (default: ollama)
  --ollama-url URL       Ollama base URL
  --temperature N        0.0–1.0 (default: 0.1 for accuracy)
  --validate-llm         Second LLM self-validation pass
  --dry-run              Extract & build prompt, skip LLM
  --print-code           Print Solidity to terminal
  -v, --verbose          Debug logging
  --install-deps         Install Python packages and exit
  --pull-model           Pull LLM model and exit
  -h, --help             Show help
```

### Python directly

```bash
python3 econtract_converter.py contract.docx
python3 econtract_converter.py contract.txt --model qwen2.5-coder:14b --output ./out
```

---

## Model Selection Guide

| Model | RAM needed | Speed | Accuracy |
|-------|-----------|-------|----------|
| `qwen2.5-coder:7b`  | ~6 GB  | ~1-3 min | Good  |
| `qwen2.5-coder:14b` | ~12 GB | ~3-7 min | Better |
| `qwen2.5-coder:32b` | ~24 GB | ~8-15 min | Best |
| `deepseek-coder:6.7b` | ~5 GB | ~1-2 min | Good |

---

## Environment Variables

```bash
export LLM_MODEL="qwen2.5-coder:7b"
export OLLAMA_BASE_URL="http://localhost:11434"
export LLM_BACKEND="ollama"

# For OpenAI-compatible backends:
export OPENAI_API_KEY="sk-..."
```

Or use the config file:

```bash
source config/econtract_converter.conf
```

---

## Output Files

```
output/
├── service_agreement_contract.sol     ← Main deliverable
├── service_agreement_contract_report.json
├── service_agreement_contract_summary.md
└── logs/
    └── conversion.log
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — all validations passed |
| 1 | Fatal error (file not found, LLM unreachable, etc.) |
| 2 | Contract generated with structural warnings — review report |

---

## Running Tests

```bash
python3 tests/test_pipeline.py
```

Tests run without an LLM (dry-run mode). All 23 tests cover:
- Text cleaning
- Party/clause/ETH address extraction
- Prompt building
- Post-processing fixes (pragma, SPDX, SafeMath, receive())
- End-to-end dry-run pipeline

---

## ⚠️ Disclaimer

Auto-generated smart contracts **must be audited by a qualified Solidity developer
before mainnet deployment**. This tool is an accelerator, not a replacement for
professional code review. The generated contracts have not been audited.

---

## Project Structure

```
econtract_converter/
├── econtract.sh              ← Main shell entrypoint
├── econtract_converter.py    ← Python CLI orchestrator
├── src/
│   ├── extractor.py          ← Document parser & preprocessor
│   ├── prompt_builder.py     ← LLM prompt engineering
│   ├── llm_client.py         ← Ollama / OpenAI LLM client
│   └── postprocessor.py      ← Solidity fixer & output writer
├── tests/
│   ├── test_pipeline.py      ← Test suite (23 tests)
│   └── sample_service_agreement.txt
├── config/
│   └── econtract_converter.conf
└── output/                   ← Generated files
```
