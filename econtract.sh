#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  econtract.sh — eContract → Smart Contract Converter  v2.0
#
#  Convert one OR many eContracts in a single command.
#  Each file gets its own Results/<filename>/ folder containing:
#    ├── <filename>.sol          ← Generated Solidity 0.8.16 smart contract
#    ├── <filename>.<ext>        ← Copy of the original input eContract
#    └── results.json            ← Conversion report, validation, metadata
#
#  Usage:
#    ./econtract.sh file1.docx file2.txt file3.docx [OPTIONS]
#
#  Examples:
#    ./econtract.sh my_contract.docx


#    ./econtract.sh my_contract.docx my_nda.txt service.docx
#    ./econtract.sh *.docx *.txt --validate-llm
#    ./econtract.sh contract.docx --model qwen2.5-coder:14b --print-code
#    ./econtract.sh contract.txt --dry-run --verbose
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'
BLU='\033[0;34m'; CYN='\033[0;36m'; WHT='\033[1;37m'
MGN='\033[0;35m'; RST='\033[0m'

info()    { echo -e "${BLU}[INFO]${RST}  $*"; }
success() { echo -e "${GRN}[OK]${RST}    $*"; }
warn()    { echo -e "${YLW}[WARN]${RST}  $*"; }
error()   { echo -e "${RED}[ERR]${RST}   $*" >&2; }
step()    { echo -e "${MGN}[►]${RST}    $*"; }
header()  { echo -e "\n${WHT}$*${RST}"; }
divider() { echo -e "${CYN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/econtract_converter.py"
RESULTS_ROOT="${SCRIPT_DIR}/Results"
LLM_MODEL="${LLM_MODEL:-qwen2.5-coder:14b}"
OLLAMA_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
LLM_BACKEND="${LLM_BACKEND:-ollama}"
TEMPERATURE="0.1"
EXTRA_ARGS=()
INPUT_FILES=()
INSTALL_DEPS=0
PULL_MODEL=0

usage() {
cat <<EOF

${WHT}eContract → Smart Contract Converter  v2.0${RST}

${CYN}Usage:${RST}
  $0 <file1.docx> [file2.txt file3.docx ...] [OPTIONS]

${CYN}Output structure (per file):${RST}
  Results/
  └── <filename>/
      ├── <filename>.sol        ← Solidity 0.8.16 smart contract
      ├── <filename>.<ext>      ← Original input eContract (copy)
      └── results.json          ← Conversion report + validation

${CYN}Options:${RST}
  -m, --model MODEL         LLM model (default: qwen2.5-coder:14b)
  --backend BACKEND         ollama|openai (default: ollama)
  --ollama-url URL          Ollama server URL
  --temperature N           LLM temperature 0–1 (default: 0.1)
  --validate-llm            Run second LLM self-validation pass
  --dry-run                 Extract & build prompt only, skip LLM
  --print-code              Print Solidity to terminal
  -v, --verbose             Enable debug logging
  --install-deps            Install Python deps and exit
  --pull-model              Pull LLM model and exit
  -h, --help                Show this help

${CYN}Examples:${RST}
  $0 my_contract.docx
  $0 my_contract.docx my_nda.txt service.docx
  $0 *.docx --validate-llm --print-code
  $0 contract.txt --dry-run --verbose

EOF
exit 0
}

# ── Argument parsing ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)        usage ;;
        --install-deps)   INSTALL_DEPS=1; shift ;;
        --pull-model)     PULL_MODEL=1; shift ;;
        -m|--model)       LLM_MODEL="$2"; shift 2 ;;
        --backend)        LLM_BACKEND="$2"; shift 2 ;;
        --ollama-url)     OLLAMA_URL="$2"; shift 2 ;;
        --temperature)    TEMPERATURE="$2"; shift 2 ;;
        --validate-llm)   EXTRA_ARGS+=("--validate-llm"); shift ;;
        --dry-run)        EXTRA_ARGS+=("--dry-run"); shift ;;
        --print-code)     EXTRA_ARGS+=("--print-code"); shift ;;
        -v|--verbose)     EXTRA_ARGS+=("--verbose"); shift ;;
        -*)               error "Unknown option: $1"; echo ""; usage ;;
        *)                INPUT_FILES+=("$1"); shift ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────────────
find_python() {
    if command -v python3 &>/dev/null; then echo "python3"
    elif command -v python &>/dev/null; then echo "python"
    else error "Python 3.8+ is required."; exit 1; fi
}

install_python_deps() {
    local PY; PY=$(find_python)
    info "Installing Python dependencies..."
    $PY -m pip install --quiet --break-system-packages \
        "python-docx>=0.8.11" "requests>=2.28" 2>/dev/null || \
    $PY -m pip install --quiet "python-docx>=0.8.11" "requests>=2.28"
    success "Dependencies installed."
}

check_python_deps() {
    local PY; PY=$(find_python)
    local missing=()
    $PY -c "import docx"     2>/dev/null || missing+=("python-docx")
    $PY -c "import requests" 2>/dev/null || missing+=("requests")
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Missing: ${missing[*]} — installing..."
        install_python_deps
    fi
}

check_ollama_running() {
    if curl -sf "${OLLAMA_URL}/api/tags" &>/dev/null; then return 0; fi
    warn "Ollama not running. Trying to start..."
    if command -v ollama &>/dev/null; then
        ollama serve &>/dev/null &
        sleep 3
        curl -sf "${OLLAMA_URL}/api/tags" &>/dev/null && return 0
    fi
    error "Cannot reach Ollama at ${OLLAMA_URL}. Run: ollama serve"
    return 1
}

check_model_available() {
    local tags; tags=$(curl -sf "${OLLAMA_URL}/api/tags" 2>/dev/null || echo "")
    echo "$tags" | grep -q "${LLM_MODEL}" && return 0
    info "Pulling model '${LLM_MODEL}'..."
    ollama pull "${LLM_MODEL}" && success "Model ready." || { error "Pull failed."; exit 1; }
}

# ── Per-file conversion ────────────────────────────────────────────────────
convert_file() {
    local INPUT_PATH="$1"
    local PY; PY=$(find_python)
    local BASENAME; BASENAME=$(basename "$INPUT_PATH")
    local STEM="${BASENAME%.*}"
    local EXT="${BASENAME##*.}"
    local EXT_LOWER="${EXT,,}"

    if [[ "$EXT_LOWER" != "docx" && "$EXT_LOWER" != "txt" ]]; then
        error "Skipping '$BASENAME' — unsupported '.${EXT}' (use .docx or .txt)"
        return 1
    fi

    local RESULT_DIR="${RESULTS_ROOT}/${STEM}"
    mkdir -p "${RESULT_DIR}"

    divider
    step "Converting: ${WHT}${BASENAME}${RST}"
    info "  Result dir  : Results/${STEM}/"
    info "  Model       : ${LLM_MODEL}"

    # 1. Copy original input eContract
    cp "$INPUT_PATH" "${RESULT_DIR}/${BASENAME}"
    success "  Input saved → Results/${STEM}/${BASENAME}"

    # 2. Run Python pipeline into a temp dir
    local TMP_OUT="${RESULT_DIR}/.tmp"
    mkdir -p "${TMP_OUT}"
    local EXIT_CODE=0

    $PY "${PYTHON_SCRIPT}" \
        "$INPUT_PATH" \
        --output      "${TMP_OUT}" \
        --model       "${LLM_MODEL}" \
        --backend     "${LLM_BACKEND}" \
        --ollama-url  "${OLLAMA_URL}" \
        --temperature "${TEMPERATURE}" \
        "${EXTRA_ARGS[@]}" \
    || EXIT_CODE=$?

    # 3. Move .sol → Results/<stem>/<stem>.sol
    local SOL_SRC; SOL_SRC=$(find "${TMP_OUT}" -maxdepth 1 -name "*.sol" 2>/dev/null | head -1 || true)
    if [[ -n "$SOL_SRC" ]]; then
        mv "$SOL_SRC" "${RESULT_DIR}/${STEM}.sol"
        success "  Contract    → Results/${STEM}/${STEM}.sol"
    else
        warn "  No .sol file produced."
    fi

    # 4. Build results.json from pipeline report + enrich it
    local JSON_SRC; JSON_SRC=$(find "${TMP_OUT}" -maxdepth 1 -name "*_report.json" 2>/dev/null | head -1 || true)
    local RESULTS_JSON="${RESULT_DIR}/results.json"

    $PY - <<PYEOF
import json, os, time

src = "$JSON_SRC"
result_dir = "$RESULT_DIR"
stem = "$STEM"
basename = "$BASENAME"
exit_code = $EXIT_CODE
sol_path = os.path.join(result_dir, stem + ".sol")
has_sol = os.path.exists(sol_path)

if src and os.path.exists(src):
    with open(src) as f:
        report = json.load(f)
else:
    report = {
        "conversion_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_file": "$INPUT_PATH",
        "contract_title": stem.replace("_", " ").title(),
        "model": "$LLM_MODEL",
        "backend": "$LLM_BACKEND",
        "elapsed_seconds": 0,
        "parties": [],
        "clauses_extracted": 0,
        "validation_issues": [],
        "validation_passed": False,
    }

# Standardise output paths
report["result_directory"]      = result_dir
report["input_econtract_file"]  = f"Results/{stem}/{basename}"
report["smart_contract_file"]   = f"Results/{stem}/{stem}.sol" if has_sol else None
report["exit_code"]             = exit_code
report["status"] = (
    "success"  if exit_code == 0 else
    "warnings" if exit_code == 2 else
    "dry_run"  if any("--dry-run" in a for a in "$EXTRA_ARGS".split()) else
    "failed"
)

with open("$RESULTS_JSON", "w") as f:
    json.dump(report, f, indent=2)
print(f"  Results JSON written ({os.path.getsize('$RESULTS_JSON')} bytes)")
PYEOF
    success "  Report      → Results/${STEM}/results.json"

    # 5. Cleanup temp
    rm -rf "${TMP_OUT}"

    # 6. Show result folder contents
    echo ""
    echo -e "     ${WHT}📁 Results/${STEM}/${RST}"
    for f in "${RESULT_DIR}"/*; do
        local SIZE; SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
        local NAME; NAME=$(basename "$f")
        local ICON="📄"
        [[ "$NAME" == *.sol ]]  && ICON="📜"
        [[ "$NAME" == *.json ]] && ICON="📋"
        echo -e "        ${CYN}${ICON} ${NAME}${RST}   (${SIZE})"
    done
    echo ""

    return $EXIT_CODE
}

# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════
header "╔══════════════════════════════════════════════════════════════╗"
header "║     eContract → Smart Contract Converter  v2.0               ║"
header "║     Solidity 0.8.16  │  Local LLM (Ollama)                   ║"
header "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Special modes
[[ "$INSTALL_DEPS" -eq 1 ]] && { install_python_deps; exit 0; }
[[ "$PULL_MODEL"   -eq 1 ]] && { check_ollama_running; check_model_available; exit 0; }

# Require input files
if [[ ${#INPUT_FILES[@]} -eq 0 ]]; then
    error "No input files specified."
    usage
fi

# Pre-flight
info "Pre-flight checks..."
check_python_deps

IS_DRY=0; [[ " ${EXTRA_ARGS[*]:-} " =~ " --dry-run " ]] && IS_DRY=1
if [[ "$LLM_BACKEND" == "ollama" && $IS_DRY -eq 0 ]]; then
    check_ollama_running || true
    check_model_available || true
fi

mkdir -p "${RESULTS_ROOT}"
info "Results root : ${RESULTS_ROOT}"
info "Files queued : ${#INPUT_FILES[@]}"
echo ""

# ── Process all files ──────────────────────────────────────────────────────
TOTAL=0; PASSED=0; WARNED=0; FAILED=0
GLOBAL_START=$(date +%s)

for FILE in "${INPUT_FILES[@]}"; do
    TOTAL=$((TOTAL + 1))
    if [[ ! -f "$FILE" ]]; then
        error "Not found: $FILE — skipping."
        FAILED=$((FAILED + 1)); continue
    fi

    TS=$(date +%s); EXIT=0
    convert_file "$FILE" || EXIT=$?
    ELAPSED=$(( $(date +%s) - TS ))

    case $EXIT in
        0) PASSED=$((PASSED + 1)); success "✓ ${FILE}  (${ELAPSED}s)" ;;
        2) WARNED=$((WARNED + 1)); warn    "⚠ ${FILE}  warnings  (${ELAPSED}s)" ;;
        *) FAILED=$((FAILED + 1)); error   "✗ ${FILE}  failed  (exit ${EXIT}, ${ELAPSED}s)" ;;
    esac
done

# ── Final summary ──────────────────────────────────────────────────────────
GLOBAL_ELAPSED=$(( $(date +%s) - GLOBAL_START ))
divider
header "BATCH SUMMARY"
echo -e "  Files processed : ${WHT}${TOTAL}${RST}"
echo -e "  ${GRN}✓ Passed        : ${PASSED}${RST}"
[[ $WARNED -gt 0 ]] && echo -e "  ${YLW}⚠ Warnings      : ${WARNED}${RST}"
[[ $FAILED -gt 0 ]] && echo -e "  ${RED}✗ Failed        : ${FAILED}${RST}"
echo -e "  Total time      : ${GLOBAL_ELAPSED}s"
echo ""
info "Results saved in: ${RESULTS_ROOT}/"
echo ""

# Tree view of results
if command -v tree &>/dev/null; then
    tree -L 2 "${RESULTS_ROOT}"
else
    for d in "${RESULTS_ROOT}"/*/; do
        echo -e "  ${WHT}$(basename "$d")/${RST}"
        ls -1 "$d" 2>/dev/null | while read -r f; do echo "    ├── $f"; done
    done
fi
divider

[[ $FAILED -gt 0 ]] && exit 1
[[ $WARNED -gt 0 ]] && exit 2
exit 0