"""
eContract Extractor — parses .docx and .txt files and returns a clean,
structured ContractDocument ready for prompt engineering.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ── optional heavy dep: python-docx ────────────────────────────────────────
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:  # pragma: no cover
    DOCX_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
#  Data model
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ContractParty:
    role: str          # e.g. "Buyer", "Seller", "Service Provider"
    name: str
    address: Optional[str] = None
    wallet_hint: Optional[str] = None   # ethereum address if present in doc


@dataclass
class ContractClause:
    index: int
    heading: str
    raw_text: str
    clause_type: str = "general"       # payment | penalty | expiry | obligation | general
    amount_eth: Optional[str] = None
    deadline_days: Optional[int] = None
    condition: Optional[str] = None


@dataclass
class ContractDocument:
    title: str
    parties: List[ContractParty]
    clauses: List[ContractClause]
    governing_law: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    currency: str = "ETH"
    full_text: str = ""
    metadata: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
#  Text-cleaning helpers
# ═══════════════════════════════════════════════════════════════════════════

# Noise patterns we strip before analysis
_NOISE_PATTERNS = [
    r"Page\s+\d+\s+of\s+\d+",
    r"CONFIDENTIAL\s*[-–—]?\s*DO\s+NOT\s+DISTRIBUTE",
    r"DRAFT\s+ONLY",
    r"^\s*[-_=]{4,}\s*$",               # horizontal rules
    r"\[SIGNATURE\s+BLOCK\]",
    r"\[INTENTIONALLY\s+LEFT\s+BLANK\]",
    r"www\.[^\s]+",                      # URLs (keep content, drop links)
    r"<[^>]+>",                          # any stray HTML tags
]

_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE | re.MULTILINE)


def _normalize_whitespace(text: str) -> str:
    # Unicode normalization → NFC
    text = unicodedata.normalize("NFC", text)
    # Replace fancy dashes / quotes with ASCII equivalents
    replacements = {
        "\u2013": "-", "\u2014": "-",      # en / em dash
        "\u2018": "'", "\u2019": "'",      # curly single quotes
        "\u201c": '"', "\u201d": '"',      # curly double quotes
        "\u00a0": " ",                     # non-breaking space
        "\u2022": "*",                     # bullet
        "\u2026": "...",                   # ellipsis
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Collapse multiple blank lines → max two
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Trim each line
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines).strip()


def _clean_text(raw: str) -> str:
    text = _NOISE_RE.sub("", raw)
    return _normalize_whitespace(text)


# ═══════════════════════════════════════════════════════════════════════════
#  Field extractors
# ═══════════════════════════════════════════════════════════════════════════

# Ethereum address pattern
_ETH_RE = re.compile(r"0x[0-9a-fA-F]{40}")

# Amounts — catch "100 ETH", "0.5 ETH", "$1,000", "USD 500"
_AMOUNT_RE = re.compile(
    r"(?:USD|ETH|USDT|DAI|\$|€|£)?\s*[\d,]+(?:\.\d+)?\s*(?:ETH|USDT|DAI|USD|dollars?|ether)?",
    re.IGNORECASE,
)

# Days / duration
_DAYS_RE = re.compile(r"(\d+)\s+(?:calendar\s+)?days?", re.IGNORECASE)

# Date patterns
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"          # 01/01/2024
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"                # 2024-01-01
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

# Party / role keywords
_ROLE_KEYWORDS = [
    "Buyer", "Seller", "Service Provider", "Client", "Vendor", "Contractor",
    "Employer", "Employee", "Licensor", "Licensee", "Lessor", "Lessee",
    "Borrower", "Lender", "Party A", "Party B", "Owner", "Developer",
]
_ROLE_RE = re.compile(
    r"(?P<role>" + "|".join(re.escape(r) for r in _ROLE_KEYWORDS) + r")"
    r'[:\s"\']*(?P<name>[A-Z][A-Za-z\s,\.]+?)(?=\s*(?:,|;|\n|and|or|hereinafter|$))',
    re.IGNORECASE,
)

# Clause heading — numbered or all-caps
_CLAUSE_HEAD_RE = re.compile(
    r"^(?:(?:Article|Section|Clause)\s+)?(?:\d+[\.\d]*\.?|[IVXLC]+\.)\s+(.+)$"
    r"|^([A-Z][A-Z\s]{4,})$",
    re.MULTILINE,
)

# Clause type classifiers
_CLAUSE_TYPES = {
    "payment":      re.compile(r"payment|price|fee|amount|invoice|compensation", re.I),
    "penalty":      re.compile(r"penalty|liquidated|damages|breach|default|forfeit", re.I),
    "expiry":       re.compile(r"term|duration|expir|terminat|renew|effectiv", re.I),
    "obligation":   re.compile(r"shall|must|obligat|warrant|guarant|represent|covenant", re.I),
    "dispute":      re.compile(r"dispute|arbitrat|mediat|jurisdiction|govern", re.I),
    "confidential": re.compile(r"confidential|non.?disclos|proprietary|secret", re.I),
    "ip":           re.compile(r"intellectual property|copyright|trademark|patent|licen", re.I),
}


def _classify_clause(text: str) -> str:
    for ctype, pat in _CLAUSE_TYPES.items():
        if pat.search(text):
            return ctype
    return "general"


def _extract_amount(text: str) -> Optional[str]:
    m = _AMOUNT_RE.search(text)
    if m:
        raw = m.group(0).strip()
        if raw and any(c.isdigit() for c in raw):
            return raw
    return None


def _extract_days(text: str) -> Optional[int]:
    m = _DAYS_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_eth_addresses(text: str) -> List[str]:
    return _ETH_RE.findall(text)


def _extract_dates(text: str) -> List[str]:
    return _DATE_RE.findall(text)


# ═══════════════════════════════════════════════════════════════════════════
#  Paragraph → clause grouping
# ═══════════════════════════════════════════════════════════════════════════

def _split_into_clauses(text: str) -> List[ContractClause]:
    """Split document text into labelled clauses."""
    clauses: List[ContractClause] = []
    current_heading = "Preamble"
    current_lines: List[str] = []
    idx = 0

    for line in text.splitlines():
        heading_match = _CLAUSE_HEAD_RE.match(line.strip())
        if heading_match:
            # Save previous clause
            body = "\n".join(current_lines).strip()
            if body:
                clause = ContractClause(
                    index=idx,
                    heading=current_heading,
                    raw_text=body,
                    clause_type=_classify_clause(current_heading + " " + body),
                    amount_eth=_extract_amount(body),
                    deadline_days=_extract_days(body),
                )
                clauses.append(clause)
                idx += 1
            current_heading = (heading_match.group(1) or heading_match.group(2) or line).strip()
            current_lines = []
        else:
            if line.strip():
                current_lines.append(line)

    # Flush last clause
    body = "\n".join(current_lines).strip()
    if body:
        clause = ContractClause(
            index=idx,
            heading=current_heading,
            raw_text=body,
            clause_type=_classify_clause(current_heading + " " + body),
            amount_eth=_extract_amount(body),
            deadline_days=_extract_days(body),
        )
        clauses.append(clause)

    return clauses if clauses else [
        ContractClause(index=0, heading="Full Contract", raw_text=text, clause_type="general")
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  Party extraction
# ═══════════════════════════════════════════════════════════════════════════

def _extract_parties(text: str) -> List[ContractParty]:
    parties: List[ContractParty] = []
    seen_roles: set = set()
    eth_addresses = _extract_eth_addresses(text)

    for m in _ROLE_RE.finditer(text):
        role = m.group("role").strip().title()
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        name = re.sub(r"[,;\.]+$", "", name)
        if not name or role.lower() in seen_roles:
            continue
        seen_roles.add(role.lower())
        wallet = eth_addresses[len(parties)] if len(parties) < len(eth_addresses) else None
        parties.append(ContractParty(role=role, name=name, wallet_hint=wallet))

    # Fallback: at least two anonymous parties
    if not parties:
        parties = [
            ContractParty(role="Party A", name="[Party A Name]"),
            ContractParty(role="Party B", name="[Party B Name]"),
        ]
    return parties


# ═══════════════════════════════════════════════════════════════════════════
#  Title extraction
# ═══════════════════════════════════════════════════════════════════════════

def _extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped
            and len(stripped) > 5
            and len(stripped) < 150
            and stripped.isupper()
            or re.search(r"agreement|contract|deed|memorandum|mou|nda|sla", stripped, re.I)
        ):
            return stripped.title()
    return "Electronic Contract"


# ═══════════════════════════════════════════════════════════════════════════
#  File readers
# ═══════════════════════════════════════════════════════════════════════════

def _read_docx(path: Path) -> str:
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx not installed. Run: pip install python-docx")
    doc = DocxDocument(str(path))
    parts: List[str] = []

    # Paragraphs
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)

    # Tables
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                parts.append(row_text)

    return "\n".join(parts)


def _read_txt(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Could not decode {path} with any known encoding.")


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

SUPPORTED_EXTENSIONS = {".docx", ".txt"}


def extract_contract(file_path: str | Path) -> ContractDocument:
    """
    Parse an eContract file (.docx or .txt) and return a structured
    ContractDocument.

    Raises:
        ValueError  — unsupported extension or unreadable file
        RuntimeError — missing python-docx when processing .docx
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise ValueError(f"File not found: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Accepted: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    # Read raw text
    raw = _read_docx(path) if ext == ".docx" else _read_txt(path)

    # Clean & preprocess
    clean = _clean_text(raw)

    # Extract structured fields
    title = _extract_title(clean)
    parties = _extract_parties(clean)
    clauses = _split_into_clauses(clean)
    dates = _extract_dates(clean)

    # Governing law
    gov_law_match = re.search(
        r"govern(?:ed|ing)\s+by(?:\s+the\s+laws?\s+of)?\s+([A-Za-z\s]+?)(?:\.|,|\n)", clean, re.I
    )
    governing_law = gov_law_match.group(1).strip() if gov_law_match else None

    return ContractDocument(
        title=title,
        parties=parties,
        clauses=clauses,
        governing_law=governing_law,
        effective_date=dates[0] if dates else None,
        expiry_date=dates[-1] if len(dates) > 1 else None,
        full_text=clean,
        metadata={
            "source_file": str(path),
            "extension": ext,
            "char_count": len(clean),
            "clause_count": len(clauses),
            "party_count": len(parties),
            "eth_addresses_found": _extract_eth_addresses(clean),
        },
    )
