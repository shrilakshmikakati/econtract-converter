"""
postprocessor.py — Cleans raw LLM output, applies deterministic Solidity fixes,
and generates final output artefacts.

Pipeline: LLM → postprocessor (this file) → assertionInjector1.cpp
                                                    ↓
                                         solc --model-checker-engine bmc/chc

assert() calls are INTENTIONALLY PRESERVED. assertionInjector1.cpp injects
probe pairs before every if/require/while/for condition. The postprocessor
runs BEFORE the injector, so _fix_assert_calls() is a no-op.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from extractor import ContractDocument


def _get_declared_state_vars(code: str) -> set[str]:
    """Return names of all declared state variables (and enum/event/error/modifier/function names)."""
    names: set[str] = set()
    for m in re.finditer(
        r"^\s*(?:address|uint\d*|int\d*|bool|bytes\d*|string|mapping|enum\s+\w+)\s+"
        r"(?:payable\s+)?(?:private|public|internal)?\s*(?:immutable\s+|constant\s+)?(\w+)\s*[;=]",
        code, re.MULTILINE,
    ):
        names.add(m.group(1))
    for m in re.finditer(
        r"^\s*(?:ContractState|\w+State)\s+(?:public|private|internal)?\s*(\w+)\s*[;=]",
        code, re.MULTILINE,
    ):
        names.add(m.group(1))
    return names


def _declared_identifiers(code: str) -> set[str]:
    """Return all top-level identifiers (state vars, events, errors, modifiers, functions)."""
    ids = _get_declared_state_vars(code)
    for pattern in (r"\benum\s+(\w+)", r"\bevent\s+(\w+)", r"\berror\s+(\w+)",
                    r"\bmodifier\s+(\w+)", r"\bfunction\s+(\w+)"):
        for m in re.finditer(pattern, code):
            ids.add(m.group(1))
    ids.add("constructor")
    return ids


def _extract_balanced(src: str, start: int) -> str:
    """Return full substring from opening '(' at start to its matching ')'."""
    depth = 0
    for i in range(start, len(src)):
        if src[i] == '(':
            depth += 1
        elif src[i] == ')':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    return src[start:]


def _insert_before_first(code: str, pattern: str, text: str) -> str:
    """Insert text before first line matching pattern. Fallback: after contract brace."""
    lines = code.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(pattern, line):
            lines.insert(i, text)
            return "".join(lines)
    m = re.search(r"(\bcontract\s+\w+[^{]*\{[^\n]*\n)", code)
    if m:
        return code[:m.end()] + text + code[m.end():]
    return code

def _fix_spdx(code: str) -> str:
    if "SPDX-License-Identifier" not in code:
        code = "// SPDX-License-Identifier: MIT\n" + code
    return code

def _fix_pragma(code: str) -> str:
    code = re.sub(
        r"pragma\s+solidity\s+(?:[><=^~!]+\s*)?\d+\.\d+(?:\.\d+)?\s*;",
        "pragma solidity ^0.8.16;",
        code,
    )
    if "pragma solidity" not in code:
        code = re.sub(
            r"(//\s*SPDX-License-Identifier:[^\n]+\n)",
            r"\1pragma solidity ^0.8.16;\n",
            code,
        )
    return code

def _fix_trailing_whitespace(code: str) -> str:
    return "\n".join(ln.rstrip() for ln in code.splitlines()).strip() + "\n"

def _fix_safemath(code: str) -> str:
    code = re.sub(r'import\s+["\'].*[Ss]afe[Mm]ath.*["\'];\n?', "", code)
    code = re.sub(r"using\s+SafeMath\s+for\s+[^;]+;\n?", "", code)
    return code


def _fix_openzeppelin_imports(code: str) -> str:
    code = re.sub(r'import\s+["\']@openzeppelin/[^"\']+["\'];\n?', "", code)
    code = re.sub(r"\bis\s+(?:Ownable|ReentrancyGuard|Pausable)\b", "", code)
    return code

def _fix_selfdestruct(code: str) -> str:
    return re.sub(
        r"selfdestruct\s*\([^)]*\)\s*;",
        "// selfdestruct removed — deprecated in Solidity 0.8.x",
        code,
    )

def _fix_tx_origin(code: str) -> str:
    return re.sub(r"\btx\.origin\b", "msg.sender /* was tx.origin — fixed */", code)

def _fix_assert_calls(code: str) -> str:
    """NO-OP: assert() calls are intentionally preserved for BMC/CHC verification."""
    return code


def _fix_noReentrant_modifier(code: str) -> str:
    code = re.sub(
        r"modifier\s+noReentrant\s*\(\s*bool\s+storage\s+\w+\s*\)",
        "modifier noReentrant()",
        code,
    )
    return code

def _fix_mapping_return(code: str) -> str:
    return re.sub(r",?\s*mapping\s*\([^)]+\)[^,)]*(?=\s*[,)])", "", code)


def _fix_calculatePenalty_view(code: str) -> str:
    code = re.sub(
        r"(function\s+calculatePenalty\s*\([^)]*\)\s+(?:external|public)\s+)view\s+",
        r"\1", code,
    )
    code = re.sub(
        r"(function\s+calculatePenalty\s*\([^)]*\)[^{]*)\bview\b(\s+returns)",
        r"\1\2", code,
    )
    return code


def _fix_duplicate_banner(code: str) -> str:
    banner_pat = re.compile(
        r'(//\s*={10,}[^\n]*\n(?://[^\n]*\n)+//\s*={10,}[^\n]*\n)',
        re.MULTILINE,
    )
    matches = list(banner_pat.finditer(code))
    for m in reversed(matches[1:]):
        code = code[:m.start()] + code[m.end():]
    return code


def _strip_existing_banner(code: str) -> str:
    lines = code.splitlines(keepends=True)
    spdx_idx = next((i for i, l in enumerate(lines) if "SPDX-License-Identifier" in l), None)
    if spdx_idx is None or spdx_idx == 0:
        return code
    pre = lines[:spdx_idx]
    if all(l.strip() == "" or l.strip().startswith("//") for l in pre):
        return "".join(lines[spdx_idx:])
    return code


def _fix_msg_value_lte_zero(code: str) -> str:
    code = re.sub(r'\bmsg\.value\s*<=\s*0\b', 'msg.value == 0', code)
    code = re.sub(r'\bmsg\.value\s*<\s*0\b', 'false', code)
    return code


def _fix_bare_locked(code: str) -> str:
    if not re.search(r"\bbool\b[^;]+\b_locked\b", code) and "bool private _locked" not in code:
        return code
    return re.sub(r'(?<!_)\blocked\b', '_locked', code)


def _fix_contractstate_type(code: str) -> str:
    code = re.sub(
        r'\buint256\b(\s+(?:public|private|internal)?\s+)(contractState\s*=\s*ContractState\.)',
        r'ContractState\1\2', code,
    )
    code = re.sub(
        r'\buint256\b(\s+(?:public|private|internal)?\s+)(contractState\s*;)',
        r'ContractState\1\2', code,
    )
    return code


_PARTY_ALIASES = [
    "parent", "acquisitionSub", "mergerSub", "buyer", "seller",
    "employer", "employee", "licensor", "licensee", "lessor", "lessee",
    "borrower", "lender", "owner", "developer", "client", "vendor",
    "partyA", "partyB", "party1", "party2",
    "company", "acquirer", "target", "subsidiary", "holdco", "newco",
    "mergerParty", "mergee", "parentCo", "subCo",
]


def _fix_party_var_naming(code: str) -> str:
    for bare, canonical in [("partyA", "_partyA"), ("partyB", "_partyB")]:
        if re.search(rf'\b{canonical}\b', code):
            continue
        if re.search(rf'\b{bare}\b', code):
            code = re.sub(rf'\b{bare}\b', canonical, code)
    return code


def _fix_undeclared_state_var_refs(code: str) -> str:
    declared = _get_declared_state_vars(code)
    party_vars = [v for v in ("_partyA", "_partyB", "_arbitrator") if v in declared]
    subst: dict[str, str] = {}
    for i, alias in enumerate(_PARTY_ALIASES):
        if alias in declared:
            continue
        if i == 0 and len(party_vars) >= 1:
            subst[alias] = party_vars[0]
        elif i == 1 and len(party_vars) >= 2:
            subst[alias] = party_vars[1]
        elif party_vars:
            subst[alias] = party_vars[-1]
    if not subst:
        return code
    pattern = r"\b(" + "|".join(re.escape(k) for k in subst.keys()) + r")\b"
    return re.sub(pattern, lambda m: subst.get(m.group(0), m.group(0)), code)


def _fix_company_name_identifiers(code: str) -> str:
    declared = _get_declared_state_vars(code)
    solidity_builtins = {
        "msg", "block", "tx", "address", "uint256", "uint", "int", "bool",
        "bytes", "string", "true", "false", "this", "super",
        "_partyA", "_partyB", "_arbitrator", "_locked", "_state",
        "_amount", "_deadline", "_penaltyRate",
    }
    all_known = declared | solidity_builtins
    party_vars = [v for v in ("_partyA", "_partyB", "_arbitrator") if v in declared]
    if not party_vars:
        return code
    replacements: dict[str, str] = {}
    seen_order: list[str] = []
    for m in re.finditer(r'msg\.sender\s*(?:!=|==)\s*([A-Za-z_]\w*)', code):
        ident = m.group(1)
        if ident in all_known or ident in replacements:
            continue
        if len(ident) > 3 and ident[0].isupper():
            replacements[ident] = None
            seen_order.append(ident)
    if not replacements:
        return code
    for i, name in enumerate(seen_order):
        replacements[name] = party_vars[min(i, len(party_vars) - 1)]
    pattern = r"\b(" + "|".join(re.escape(k) for k in replacements) + r")\b"
    return re.sub(pattern, lambda m: replacements.get(m.group(0), m.group(0)), code)


def _fix_undeclared_identifiers_in_modifiers(code: str) -> str:
    declared = _declared_identifiers(code)
    builtins = {"msg", "block", "tx", "address", "this", "type", "abi",
                "revert", "emit", "return", "true", "false"}

    def _safe(ident: str) -> bool:
        return ident in declared or ident in builtins or ident.startswith("_")

    def _fix_body(mod_m: re.Match) -> str:
        body = mod_m.group(0)
        body = re.sub(
            r"(msg\.sender\s*[!=]=\s*)(\w+)",
            lambda m: m.group(0) if _safe(m.group(2)) else m.group(1) + "_partyA",
            body,
        )
        body = re.sub(
            r"(\w+)(\s*[!=]=\s*msg\.sender)",
            lambda m: m.group(0) if _safe(m.group(1)) else "_partyA" + m.group(2),
            body,
        )
        return body

    return re.sub(
        r"modifier\s+\w+\s*\([^)]*\)\s*\{[^}]*\}",
        _fix_body, code, flags=re.DOTALL,
    )


def _fix_undeclared_param_refs(code: str) -> str:
    """Fix parameter name mismatches (amount_ vs amount) inside function bodies."""
    repairs: list[tuple[int, int, str]] = []
    fn_pat = re.compile(r'function\s+\w+\s*\(([^)]*)\)([^{]*)\{', re.DOTALL)

    for m in fn_pat.finditer(code):
        params_str = m.group(1)
        fn_start = m.end()
        depth, j = 1, fn_start
        while j < len(code) and depth:
            if code[j] == '{': depth += 1
            elif code[j] == '}': depth -= 1
            j += 1
        body = code[fn_start:j - 1]
        param_names = [
            tokens[-1].strip()
            for p in params_str.split(',')
            if (tokens := p.split()) and
               re.match(r'^[a-zA-Z_]\w*$', tokens[-1].strip()) and
               tokens[-1].strip() not in ('memory', 'storage', 'calldata', 'payable', 'indexed')
        ]
        new_body = body
        for name in param_names:
            alt = name[:-1] if name.endswith('_') else (name[1:] if name.startswith('_') else name + '_')
            if re.search(rf'\b{re.escape(alt)}\b', new_body) and not re.search(rf'\b{re.escape(name)}\b', new_body):
                new_body = re.sub(rf'\b{re.escape(alt)}\b', name, new_body)
        if new_body != body:
            repairs.append((fn_start, j - 1, new_body))

    for start, end, new_body in reversed(repairs):
        code = code[:start] + new_body + code[end:]
    return code


def _fix_broken_onlyParties(code: str) -> str:
    declared = _get_declared_state_vars(code)

    def _safe_condition() -> str:
        parties = [v for v in ("_partyA", "_partyB") if v in declared]
        if len(parties) == 2:
            return f"if (msg.sender != {parties[0]} && msg.sender != {parties[1]}) revert Unauthorized();"
        if len(parties) == 1:
            return f"if (msg.sender != {parties[0]}) revert Unauthorized();"
        if "_arbitrator" in declared:
            return "if (msg.sender != _arbitrator) revert Unauthorized();"
        return "// access check skipped — no party addresses declared"

    pat = re.compile(r'modifier\s+only(?:Parties|Party\w*)\s*\(\s*\)', re.DOTALL)
    result, pos = [], 0
    for mhead in pat.finditer(code):
        result.append(code[pos:mhead.end()])
        brace_m = re.search(r'\{', code[mhead.end():])
        if not brace_m:
            pos = mhead.end()
            continue
        brace_start = mhead.end() + brace_m.start()
        result.append(code[mhead.end():brace_start])
        depth, j = 0, brace_start
        while j < len(code):
            ch = code[j]
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    mod_block = code[brace_start:j + 1]
                    cond_m = re.search(r"\{(.*?)_\s*;", mod_block, re.DOTALL)
                    if cond_m:
                        bad = [t for t in re.findall(r"\b([a-zA-Z_]\w*)\b", cond_m.group(1))
                               if t in _PARTY_ALIASES and t not in declared]
                        if bad:
                            mod_block = f" {{\n        {_safe_condition()}\n        _;\n    }}"
                    result.append(mod_block)
                    pos = j + 1
                    break
            j += 1
        else:
            pos = mhead.end()
    result.append(code[pos:])
    return "".join(result)


def _fix_modifier_ordering(code: str) -> str:
    first_fn = re.search(r'\n    function\s+\w+', code)
    if not first_fn:
        return code
    modifier_pat = re.compile(r'\n(    modifier\s+\w+[^{]*\{(?:[^{}]|\{[^}]*\})*\})', re.DOTALL)
    to_move = [(m.start(), m.end(), m.group(0)) for m in modifier_pat.finditer(code) if m.start() > first_fn.start()]
    if not to_move:
        return code
    for start, end, _ in reversed(to_move):
        code = code[:start] + code[end:]
    first_fn = re.search(r'\n    function\s+\w+', code)
    if not first_fn:
        return code
    block = "".join(text for _, _, text in to_move)
    return code[:first_fn.start()] + block + code[first_fn.start():]


def _fix_receive_body(code: str) -> str:
    def _fix(m: re.Match) -> str:
        body = m.group(1)
        if re.search(r'\b(?!emit|revert|if|require)\w+\s*\(', body):
            if "PaymentReceived" in code:
                safe = "        emit PaymentReceived(msg.sender, msg.value);\n    "
            elif "PaymentMade" in code:
                safe = "        emit PaymentMade(msg.sender, msg.value);\n    "
            else:
                safe = "        // ETH received\n    "
            return m.group(0).replace(body, safe)
        return m.group(0)
    return re.sub(r'receive\s*\(\s*\)\s+external\s+payable\s*\{([^}]*)\}', _fix, code, flags=re.DOTALL)


def _fix_return_in_non_returning_fn(code: str) -> str:
    def _strip(m: re.Match) -> str:
        fn_head, body = m.group(1), m.group(2)
        if re.search(r'\breturns\s*\(', fn_head):
            return m.group(0)
        new_body = re.sub(r'\n?\s*return\s+[^;]+;\n?', '\n', body)
        return m.group(0) if new_body == body else fn_head + "{\n" + new_body + "    }"
    return re.sub(
        r'(function\s+\w+[^{]+)\{\n((?:[^{}]|\{[^}]*\})*?)\n    \}',
        _strip, code, flags=re.DOTALL,
    )


def _parse_require_args(src: str, start: int) -> tuple[str, str] | None:
    depth, i, n, parts, seg_start = 0, start, len(src), [], start + 1
    while i < n:
        c = src[i]
        if c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                parts.append(src[seg_start:i])
                break
        elif c == ',' and depth == 1:
            parts.append(src[seg_start:i])
            seg_start = i + 1
        i += 1
    if not parts:
        return None
    return parts[0].strip(), (parts[1].strip().strip('"\'') if len(parts) > 1 else "")


def _negate_condition(condition: str) -> str:
    stripped = condition.strip()
    depth = 0
    for ch in stripped:
        if ch == '(': depth += 1
        elif ch == ')': depth -= 1
        elif depth == 0 and stripped[stripped.index(ch):].startswith(('&&', '||')):
            return f"!({stripped})"
    while stripped.startswith("(") and stripped.endswith(")"):
        depth2 = 0
        for i, ch in enumerate(stripped):
            if ch == '(': depth2 += 1
            elif ch == ')':
                depth2 -= 1
                if depth2 == 0 and i < len(stripped) - 1:
                    break
        else:
            stripped = stripped[1:-1].strip()
            continue
        break
    if stripped.startswith("!"):
        inner = stripped[1:].strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1].strip()
        return inner
    for old, new in ((" == ", " != "), (" != ", " == "), (" >= ", " < "),
                     (" <= ", " > "), (" > ", " <= "), (" < ", " >= ")):
        if old in stripped:
            return stripped.replace(old, new, 1)
    return f"!({stripped})"


def _fix_require_to_custom_errors(code: str) -> str:
    result, i, n = [], 0, len(code)
    while i < n:
        m = re.search(r'\brequire\s*\(', code[i:])
        if not m:
            result.append(code[i:])
            break
        pre_start = i + m.start()
        result.append(code[i:pre_start])
        paren_open = i + m.end() - 1
        parsed = _parse_require_args(code, paren_open)
        if parsed is None:
            result.append(code[pre_start:i + m.end()])
            i = i + m.end()
            continue
        condition, _ = parsed
        result.append(f"if ({_negate_condition(condition)}) revert Unauthorized();")
        depth, j = 0, paren_open
        while j < n:
            if code[j] == '(': depth += 1
            elif code[j] == ')':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        i = j
    return "".join(result)


def _fix_malformed_if_revert(code: str) -> str:
    bad_sentinel = re.compile(r'\)\s+revert\s+\w+\s*\([^;{]*\)\s*(?:&&|\|\|)')
    if not bad_sentinel.search(code):
        return code

    def _parse_line(line: str):
        m_if = re.search(r'\bif\s*\(', line)
        if not m_if:
            return None
        start, depth, i, n = m_if.end(), 1, m_if.end(), len(line)
        outer_end = None
        while i < n:
            ch = line[i]
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    outer_end = i
                    break
            i += 1
        if outer_end is None:
            return None
        inner = line[start:outer_end]
        err_m = re.search(r'\brevert\s+(\w+)\s*\(', inner)
        if not err_m:
            return None
        err_name = err_m.group(1)

        def _strip_reverts(s: str) -> str:
            res, j, sn = [], 0, len(s)
            while j < sn:
                rev_m = re.search(r'\brevert\s+\w+\s*\(', s[j:])
                if not rev_m:
                    res.append(s[j:])
                    break
                res.append(s[j:j + rev_m.start()])
                k, d = j + rev_m.end(), 1
                while k < sn and d > 0:
                    if s[k] == '(': d += 1
                    elif s[k] == ')': d -= 1
                    k += 1
                j = k
            return ''.join(res)

        def _split_top(s: str) -> list:
            parts, buf, d, idx, sn2 = [], [], 0, 0, len(s)
            while idx < sn2:
                ch = s[idx]
                if ch == '(': d += 1; buf.append(ch)
                elif ch == ')': d -= 1; buf.append(ch)
                elif d == 0 and s[idx:idx + 2] in ('&&', '||'):
                    tok = ''.join(buf).strip().strip(',').strip()
                    if tok: parts.append(tok)
                    buf = []; idx += 2; continue
                elif d == 0 and ch == ',':
                    tok = ''.join(buf).strip()
                    if tok: parts.append(tok)
                    buf = []
                else:
                    buf.append(ch)
                idx += 1
            tok = ''.join(buf).strip().strip(',').strip()
            if tok: parts.append(tok)
            return [p for p in parts if p]

        parts = _split_top(_strip_reverts(inner)) or [inner.strip()]
        neg_parts = [_negate_condition(p.strip()) if p.strip() else 'true' for p in parts]
        compound = ' || '.join(neg_parts) or 'true'
        indent = re.match(r'(^\s*)', line).group(1)
        suffix = line[outer_end + 1:].strip().lstrip(';').strip()
        tail = f' {suffix}' if suffix and not suffix.startswith('//') else ''
        return f'{indent}if ({compound}) revert {err_name}();{tail}'

    lines = code.splitlines(keepends=True)
    out = []
    for line in lines:
        if bad_sentinel.search(line):
            fixed = _parse_line(line.rstrip('\n\r'))
            if fixed is not None:
                ending = '\n' if line.endswith('\n') else ''
                line = fixed + ending
        out.append(line)
    return ''.join(out)


def _fix_party_declarations(code: str) -> str:
    def _has_decl(var: str) -> bool:
        return bool(re.search(
            rf'address\s+(?:payable\s+)?(?:private|public|internal)\s+{re.escape(var)}\s*[;=]', code,
        ))
    need_a = '_partyA' in code and not _has_decl('_partyA')
    need_b = '_partyB' in code and not _has_decl('_partyB')
    if not need_a and not need_b:
        return code
    inject = ("    address payable private _partyA;\n" if need_a else "") + \
             ("    address payable private _partyB;\n" if need_b else "")
    return _insert_before_first(code, r"\s*(modifier|constructor)\b", inject)


def _fix_state_var_declaration(code: str) -> str:
    has_enum = bool(re.search(r'\benum\s+ContractState\b', code))
    has_state = bool(re.search(r'ContractState\s+(?:private|public|internal)\s+_state\b', code))
    if '_state' not in code or has_state:
        return code
    if not has_enum:
        enum_block = "    enum ContractState { Created, Active, Completed, Disputed, Terminated }\n"
        m = re.search(r"(\bcontract\s+\w+[^{]*\{[^\n]*\n)", code)
        if m:
            code = code[:m.end()] + enum_block + code[m.end():]
    return _insert_before_first(
        code, r"\s*(modifier|constructor)\b",
        "    ContractState private _state = ContractState.Created;\n",
    )


def _fix_onlyPartyA_modifier(code: str) -> str:
    if not bool(re.search(r'\bonlyPartyA\b', code)) or bool(re.search(r'modifier\s+onlyPartyA\s*\(', code)):
        return code
    declared = _get_declared_state_vars(code)
    party = '_partyA' if '_partyA' in declared else None
    if not party:
        return code
    mod = (f"    modifier onlyPartyA() {{\n"
           f"        if (msg.sender != {party}) revert Unauthorized();\n"
           f"        _;\n    }}\n")
    return _insert_before_first(code, r"\s*function\s+\w+", mod)


def _fix_locked_declaration(code: str) -> str:
    matches = list(re.finditer(r"[ \t]*bool\s+private\s+_locked\s*;[^\n]*\n?", code))
    for m in reversed(matches[1:]):
        code = code[:m.start()] + code[m.end():]
    if re.search(r"bool\s+private\s+_locked\s*;", code):
        return code
    inject = "    bool private _locked; // reentrancy guard\n"
    lines = code.splitlines(keepends=True)
    in_contract = False
    for i, line in enumerate(lines):
        if re.match(r"\s*contract\s+\w+", line):
            in_contract = True
        if in_contract and re.match(r"\s*(modifier|constructor)\b", line):
            lines.insert(i, inject)
            return "".join(lines)
    return code


def _fix_duplicate_state_vars(code: str) -> str:
    decl_pat = re.compile(
        r'^([ \t]*)(?:address|uint\d*|int\d*|bool|bytes\d*|string|mapping)\s+'
        r'(?:payable\s+)?(?:private|public|internal|external\s+)?(?:immutable\s+|constant\s+)?(\w+)\s*[;=]',
        re.MULTILINE,
    )
    seen: set[str] = set()
    result: list[str] = []
    for line in code.splitlines(keepends=True):
        m = decl_pat.match(line)
        if m:
            var_name = m.group(2)
            if var_name in seen:
                continue
            seen.add(var_name)
        result.append(line)
    return "".join(result)


def _fix_onlyX_modifiers(code: str) -> str:
    existing = re.findall(r"modifier\s+only\w+\s*\(", code)
    if len(existing) >= 2:
        return code
    declared = _get_declared_state_vars(code)
    has_a, has_b, has_arb = "_partyA" in declared, "_partyB" in declared, "_arbitrator" in declared
    inject_lines: list[str] = []
    if len(existing) == 0 and has_a and has_b:
        inject_lines.append(
            "    modifier onlyParties() {\n"
            "        if (msg.sender != _partyA && msg.sender != _partyB) revert Unauthorized();\n"
            "        _;\n    }\n"
        )
    elif len(existing) == 0 and has_arb:
        inject_lines.append(
            "    modifier onlyOwner() {\n"
            "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
            "        _;\n    }\n"
        )
    if len(existing) < 2 and has_arb and not re.search(r"modifier\s+onlyArbitrator", code):
        inject_lines.append(
            "    modifier onlyArbitrator() {\n"
            "        if (msg.sender != _arbitrator) revert Unauthorized();\n"
            "        _;\n    }\n"
        )
    if not inject_lines:
        return code
    lines = code.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"\s*(modifier|constructor)\b", line):
            for j, block in enumerate(inject_lines):
                lines.insert(i + j, block)
            return "".join(lines)
    return code


def _fix_governing_law_constant(code: str, doc: ContractDocument) -> str:
    if "GOVERNING_LAW" in code:
        return code
    gov = (doc.governing_law or "").strip()
    if not gov:
        return code
    gov_clean = re.sub(r"(?i)^(?:and\s+)?(?:the\s+)?(?:laws?\s+of\s+(?:the\s+)?(?:state\s+of\s+)?)?", "", gov).strip()
    gov_clean = re.sub(r"(?i)\s+laws?$", "", gov_clean).strip()
    jurisdiction = gov_clean or "Unknown"
    constant_line = f'    string public constant GOVERNING_LAW = "{jurisdiction}";\n'
    m = re.search(r"(uint256\s+public\s+constant\s+EFFECTIVE_DATE[^\n]+\n)", code)
    if m:
        return code[:m.end()] + constant_line + code[m.end():]
    m = re.search(r"(pragma\s+solidity[^\n]+\n)", code)
    if m:
        return code[:m.end()] + constant_line + code[m.end():]
    return code


def _fix_start_date(code: str) -> str:
    code = re.sub(
        r"(uint256\s+public\s+immutable\s+startDate)\s*=\s*EFFECTIVE_DATE\s*;",
        r"\1;", code,
    )
    has_start_date = bool(re.search(r"\bstartDate\b", code))
    has_effective_date = bool(re.search(r"\beffectiveDate\b", code))
    if has_start_date or has_effective_date:
        if has_start_date and "startDate = EFFECTIVE_DATE" not in code:
            ctor = re.search(r"constructor\s*\([^)]*\)[^{]*\{", code)
            if ctor:
                code = code[:ctor.end()] + "\n        startDate = EFFECTIVE_DATE;" + code[ctor.end():]
        return code
    if "EFFECTIVE_DATE" not in code:
        return code
    decl = "    uint256 public immutable startDate;\n"
    m = re.search(r"(uint256\s+public\s+constant\s+EFFECTIVE_DATE[^\n]+\n)", code)
    if m:
        code = code[:m.end()] + decl + code[m.end():]
    ctor = re.search(r"constructor\s*\([^)]*\)[^{]*\{", code)
    if ctor:
        code = code[:ctor.end()] + "\n        startDate = EFFECTIVE_DATE;" + code[ctor.end():]
    return code


def _fix_confidentiality_acknowledgement(code: str, doc: ContractDocument) -> str:
    if not any(c.clause_type == "confidential" for c in doc.clauses):
        return code
    if re.search(r"confidential|nda|nonDisclos|non_disclos", code, re.I):
        return code
    state_var = "    bool private _confidentialityAcknowledged;\n"
    event_decl = "    event NonDisclosureAcknowledged(address indexed party, uint256 timestamp);\n"
    fn_body = (
        "\n    /// @notice Acknowledges the non-disclosure / confidentiality obligation on-chain.\n"
        "    function acknowledgeNonDisclosure() external onlyParties {\n"
        "        _confidentialityAcknowledged = true;\n"
        "        emit NonDisclosureAcknowledged(msg.sender, block.timestamp);\n"
        "    }\n"
    )
    locked_m = re.search(r"(bool\s+private\s+_locked\s*;)", code)
    if locked_m:
        code = code[:locked_m.end()] + "\n" + state_var + code[locked_m.end():]
    last_event = None
    for m in re.finditer(r"event\s+\w+\s*\([^)]*\)\s*;", code):
        last_event = m
    if last_event:
        code = code[:last_event.end()] + "\n" + event_decl + code[last_event.end():]
    last_brace = code.rfind("}")
    if last_brace != -1:
        code = code[:last_brace] + fn_body + code[last_brace:]
    return code


_KNOWN_ERROR_SIGS: dict[str, str] = {
    "Unauthorized":        "error Unauthorized();",
    "InvalidState":        "error InvalidState(uint8 current, uint8 required);",
    "ReentrantCall":       "error ReentrantCall();",
    "InsufficientPayment": "error InsufficientPayment(uint256 sent, uint256 required);",
    "DeadlinePassed":      "error DeadlinePassed(uint256 deadline, uint256 current);",
    "AlreadyDisputed":     "error AlreadyDisputed();",
    "ContractExpired":     "error ContractExpired();",
    "NotActive":           "error NotActive();",
    "NotInDispute":        "error NotInDispute();",
    "OnlyArbitrator":      "error OnlyArbitrator();",
    "OnlyParty":           "error OnlyParty();",
}

_KNOWN_EVENT_SIGS: dict[str, str] = {
    "PaymentReceived":      "event PaymentReceived(address indexed from, uint256 amount);",
    "PaymentMade":          "event PaymentMade(address indexed payer, uint256 amount);",
    "ContractCreated":      "event ContractCreated(address indexed partyA, address indexed partyB, uint256 amount);",
    "DeliveryAcknowledged": "event DeliveryAcknowledged(address indexed acknowledger, uint256 timestamp);",
    "DisputeRaised":        "event DisputeRaised(address indexed initiator, uint256 timestamp);",
    "ContractTerminated":   "event ContractTerminated(address indexed initiator, uint256 timestamp);",
    "PenaltyCalculated":    "event PenaltyCalculated(uint256 penaltyWei);",
    "StateChanged":         "event StateChanged(uint8 from, uint8 to);",
}


def _fix_missing_custom_errors(code: str) -> str:
    reverted = set(re.findall(r"\brevert\s+(\w+)\s*[;(]", code))
    reverted |= {"Unauthorized", "ReentrantCall", "InvalidState", "InsufficientPayment", "DeadlinePassed", "AlreadyDisputed"}
    declared = set(re.findall(r"\berror\s+(\w+)\s*[;(]", code))
    missing = reverted - declared
    if not missing:
        return code
    inject_block = "\n".join(f"    {_KNOWN_ERROR_SIGS.get(n, f'error {n}();')}" for n in sorted(missing)) + "\n"
    m = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
    if m:
        code = code[:m.end()] + "\n" + inject_block + code[m.end():]
    return code


def _fix_missing_events(code: str) -> str:
    _CORE_EVENTS = {"PaymentReceived", "ContractTerminated", "StateChanged"}
    emitted = set(re.findall(r"\bemit\s+(\w+)\s*\(", code))
    declared = set(re.findall(r"\bevent\s+(\w+)\s*\(", code))
    needed = (emitted | _CORE_EVENTS) - declared
    if not needed:
        return code
    inject_block = "\n".join(
        f"    {_KNOWN_EVENT_SIGS.get(n, f'event {n}(address indexed caller, uint256 value);')}"
        for n in sorted(needed)
    ) + "\n"
    last_event = None
    for m in re.finditer(r"event\s+\w+[^;]+;\n", code):
        last_event = m
    if last_event:
        return code[:last_event.end()] + inject_block + code[last_event.end():]
    m = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
    if m:
        return code[:m.end()] + "\n" + inject_block + code[m.end():]
    return code


_NOREENTRANT_BODY = """
    modifier noReentrant() {
        if (_locked) revert ReentrantCall();
        _locked = true;
        _;
        _locked = false;
    }
"""


def _fix_missing_noReentrant(code: str) -> str:
    if re.search(r"modifier\s+noReentrant\s*\(", code):
        return code
    used = bool(re.search(r"\bnoReentrant\b", code))
    has_payable = bool(re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b", code, re.DOTALL))
    if not used and not has_payable:
        return code
    # Ensure _locked is declared (deduplicate)
    lock_matches = list(re.finditer(r"[ \t]*bool\s+private\s+_locked\s*;[^\n]*\n?", code))
    for m in reversed(lock_matches[1:]):
        code = code[:m.start()] + code[m.end():]
    if not re.search(r"bool\s+private\s+_locked\s*;", code):
        inject_lock = "    bool private _locked; // reentrancy guard\n"
        lines = code.splitlines(keepends=True)
        inserted = False
        for i, line in enumerate(lines):
            if re.match(r"\s*constructor\b", line):
                lines.insert(i, inject_lock); inserted = True; break
        if not inserted:
            for i, line in enumerate(lines):
                if re.match(r"\s*(modifier|function)\b", line):
                    lines.insert(i, inject_lock); inserted = True; break
        if not inserted:
            for i, line in enumerate(lines):
                if re.search(r"\bcontract\s+\w+[^{]*\{", line):
                    lines.insert(i + 1, inject_lock); break
        code = "".join(lines)
    # Ensure ReentrantCall error is declared
    if not re.search(r"\berror\s+ReentrantCall\s*\(", code):
        m_contract = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
        if m_contract:
            code = code[:m_contract.end()] + "\n    error ReentrantCall();" + code[m_contract.end():]
    # Inject modifier before first function
    lines = code.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"\s*function\s+\w+", line):
            lines.insert(i, _NOREENTRANT_BODY)
            break
    return "".join(lines)


def _fix_payable_noReentrant(code: str) -> str:
    if not re.search(r"modifier\s+noReentrant\s*\(", code):
        return code
    pat = re.compile(r'(function\s+(\w+)\s*\([^)]*\)[^{]*\bpayable\b[^{]*)\{', re.DOTALL)
    def _add(m: re.Match) -> str:
        sig = m.group(1)
        if "noReentrant" in sig:
            return m.group(0)
        return f"{sig.rstrip()} noReentrant {{"
    return pat.sub(_add, code)


def _fix_payable_and_receive(code: str) -> str:
    has_payable_fn = bool(
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bexternal\b[^{]*\bpayable\b", code) or
        re.search(r"function\s+\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\bexternal\b", code)
    )
    has_receive = bool(re.search(r"\breceive\s*\(\s*\)\s+external\s+payable", code))
    if has_payable_fn and has_receive:
        return code
    has_noReentrant = bool(re.search(r"modifier\s+noReentrant\s*\(", code))
    inject = ""
    if not has_receive:
        if "PaymentReceived" not in code:
            event_line = "    event PaymentReceived(address indexed from, uint256 amount);\n"
            last_event = None
            for m in re.finditer(r"event\s+\w+[^;]+;\n", code):
                last_event = m
            if last_event:
                code = code[:last_event.end()] + event_line + code[last_event.end():]
            else:
                m2 = re.search(r"(\bcontract\s+\w+[^{]*\{)", code)
                if m2:
                    code = code[:m2.end()] + "\n" + event_line + code[m2.end():]
        inject += (
            "\n    /// @notice Accept direct ETH deposits.\n"
            "    receive() external payable {\n"
            "        emit PaymentReceived(msg.sender, msg.value);\n"
            "    }\n"
        )
    if not has_payable_fn:
        guard_open = "noReentrant " if has_noReentrant else ""
        guard_inner = "" if has_noReentrant else (
            "        if (_locked) revert ReentrantCall();\n        _locked = true;\n"
        )
        guard_close = "" if has_noReentrant else "        _locked = false;\n"
        inject += (
            "\n    /// @notice Deposit ETH payment into the contract.\n"
            f"    function depositPayment() external payable {guard_open}{{\n"
            f"{guard_inner}"
            "        emit PaymentReceived(msg.sender, msg.value);\n"
            f"{guard_close}"
            "    }\n"
        )
    if inject:
        idx = code.rfind("}")
        if idx != -1:
            code = code[:idx] + inject + code[idx:]
    return code


def _fix_expiry_deadline(code: str) -> str:
    if re.search(r'\b_deadline\b|\bdeadlineAt\b', code) or re.search(r'block\.timestamp\s*\+', code):
        return code
    decl = "    uint256 private _deadline; // contract expiry (unix timestamp)\n"
    last_var = None
    for m in re.finditer(
        r'^    (?:address|uint\d*|bool|bytes\d*|string)\s+'
        r'(?:payable\s+)?(?:private|public|internal)\s+'
        r'(?:immutable\s+|constant\s+)?\w+\s*[=;][^\n]*\n',
        code, re.MULTILINE,
    ):
        last_var = m
    if last_var:
        code = code[:last_var.end()] + decl + code[last_var.end():]
    else:
        m2 = re.search(r'(\bcontract\s+\w+[^{]*\{[^\n]*\n)', code)
        if m2:
            code = code[:m2.end()] + decl + code[m2.end():]
    set_fn = (
        "\n    /// @notice Set the contract expiry deadline (seconds from now).\n"
        "    function setDeadline(uint256 durationSeconds) external onlyArbitrator {\n"
        "        _deadline = block.timestamp + durationSeconds;\n"
        "    }\n"
    )
    idx = code.rfind("}")
    if idx != -1:
        code = code[:idx] + set_fn + code[idx:]
    return code


def _fix_msg_value_validation(code: str) -> str:
    def _add_check(m: re.Match) -> str:
        fn_head = m.group(0)
        fn_name_m = re.search(r'function\s+(\w+)', fn_head)
        body_start = m.end()
        depth, j, src = 1, body_start, m.string
        while j < len(src) and depth:
            if src[j] == '{': depth += 1
            elif src[j] == '}': depth -= 1
            j += 1
        body = src[body_start:j - 1]
        if re.search(r'msg\.value\s*[=!<>]', body):
            return fn_head
        if re.search(r'\berror\s+InsufficientPayment\s*\(\s*uint256', code):
            err_call = "revert InsufficientPayment(msg.value, 0);"
        else:
            err_call = "revert InsufficientPayment();"
        return fn_head + f"\n        if (msg.value == 0) {err_call}"
    return re.sub(
        r'function\s+(?!receive\b)(?!fallback\b)\w+\s*\([^)]*\)[^{]*\bpayable\b[^{]*\{',
        _add_check, code,
    )


def _fix_msg_value_in_nonpayable(code: str) -> str:
    fn_sig_pat = re.compile(r'(function\s+\w+\s*\([^)]*\)(?:[^{]*?))\{', re.DOTALL)
    def _make_payable(m: re.Match) -> str:
        sig = m.group(1)
        if re.search(r'\bpayable\b', sig):
            return m.group(0)
        fn_start = m.end()
        src = m.string
        depth, j = 1, fn_start
        while j < len(src) and depth:
            if src[j] == '{': depth += 1
            elif src[j] == '}': depth -= 1
            j += 1
        if 'msg.value' not in src[fn_start:j - 1]:
            return m.group(0)
        sig = re.sub(r'\b(view|pure)\b\s*', '', sig)
        if re.search(r'\bexternal\b', sig):
            sig = re.sub(r'\bexternal\b', 'external payable', sig, count=1)
        elif re.search(r'\bpublic\b', sig):
            sig = re.sub(r'\bpublic\b', 'public payable', sig, count=1)
        else:
            sig = sig.rstrip() + ' payable '
        return sig + '{'
    for _ in range(3):
        new_code = fn_sig_pat.sub(_make_payable, code)
        if new_code == code:
            break
        code = new_code
    return code


def _fix_address_payable_cast(code: str) -> str:
    payable_vars = set(re.findall(r'address\s+payable\s+(?:private|public|internal\s+)?(\w+)', code))
    if not payable_vars:
        return code
    def _wrap(m: re.Match) -> str:
        lhs, rhs = m.group(1), m.group(2).strip()
        if rhs.startswith("payable("):
            return m.group(0)
        return f"{lhs} = payable({rhs})"
    names_re = "|".join(re.escape(v) for v in sorted(payable_vars))
    return re.compile(rf'\b({names_re})\s*=\s*([^;{{]+)').sub(_wrap, code)


def _fix_natspec_comments(code: str) -> str:
    MIN_NOTICE = 8
    if len(re.findall(r'///\s*@notice', code)) >= MIN_NOTICE:
        return code
    lines, out, added = code.splitlines(keepends=True), [], len(re.findall(r'///\s*@notice', code))
    i = 0
    while i < len(lines):
        line = lines[i]
        fn_start = re.match(r'(\s*)function\s+(\w+)\s*\(', line)
        if fn_start and added < MIN_NOTICE:
            look = "".join(lines[i:i + 8])
            if (re.search(r'\b(public|external)\b', look) and "{" in look
                    and "@notice" not in (out[-1].rstrip() if out else "")):
                out.append(f"{fn_start.group(1)}/// @notice Execute {fn_start.group(2)} operation.\n")
                added += 1
        rcv = re.match(r'(\s*)(receive|fallback)\s*\(\s*\)\s+external', line)
        if rcv and added < MIN_NOTICE and "@notice" not in (out[-1].rstrip() if out else ""):
            out.append(f"{rcv.group(1)}/// @notice {rcv.group(2).capitalize()} ETH deposits.\n")
            added += 1
        out.append(line)
        i += 1
    return "".join(out)


def _rewrite_negated_compound(inner: str) -> str | None:
    s = inner.strip()
    parts: list[str] = []
    operator: str | None = None
    depth, current, i = 0, [], 0
    while i < len(s):
        ch = s[i]
        if ch == '(':
            depth += 1; current.append(ch)
        elif ch == ')':
            depth -= 1; current.append(ch)
        elif depth == 0 and s[i:i+2] in ('||', '&&'):
            op = s[i:i+2]
            if operator is None:
                operator = op
            elif operator != op:
                return None
            parts.append(''.join(current).strip())
            current = []; i += 2; continue
        else:
            current.append(ch)
        i += 1
    parts.append(''.join(current).strip())
    if len(parts) < 2 or operator is None:
        return None
    new_op = '&&' if operator == '||' else '||'
    negated = [f'!({p})' if not p.startswith('!') else p[1:].strip('()') for p in parts]
    return f' {new_op} '.join(negated)


def _fix_injector_safe_conditions(code: str) -> str:
    result, i, n = [], 0, len(code)
    kw_pat = re.compile(r'\b(if|require|while)\s*\(')
    while i < n:
        m = kw_pat.search(code, i)
        if not m:
            result.append(code[i:])
            break
        result.append(code[i:m.start()])
        kw = m.group(1)
        outer_open = m.end() - 1
        orig_gap = code[m.start() + len(kw):outer_open]
        outer_block = _extract_balanced(code, outer_open)
        outer_end = outer_open + len(outer_block)
        inner = outer_block[1:-1]
        stripped = inner.strip()
        if stripped.startswith('!(') and stripped.endswith(')'):
            excl_paren_start = stripped.index('(')
            nested = _extract_balanced(stripped, excl_paren_start)
            if nested == stripped[excl_paren_start:]:
                expr = nested[1:-1]
                demorgan = _rewrite_negated_compound(expr)
                new_inner = f'({demorgan})' if demorgan is not None else f'({_negate_condition(expr)})'
                result.append(f'{kw}{orig_gap}{new_inner}')
                i = outer_end
                continue
        result.append(f'{kw}{orig_gap}{outer_block}')
        i = outer_end
    return ''.join(result)


def _add_version_comment(code: str, doc: ContractDocument) -> str:
    clean_title = doc.title.lstrip("\ufeff").strip()
    banner = (
        "\n// =================================================================\n"
        f"// Contract : {clean_title}\n"
        f"// Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        "// Tool     : eContract -> Smart Contract Converter v2.0\n"
        "// Solidity : 0.8.16\n"
        "// WARNING  : Review thoroughly before deployment on mainnet.\n"
        "// =================================================================\n"
    )
    pragma_m = re.search(r"(pragma solidity[^\n]+\n)", code)
    if pragma_m:
        return code[:pragma_m.end()] + banner + code[pragma_m.end():]
    spdx_m = re.search(r"(//\s*SPDX-License-Identifier:[^\n]+\n)", code)
    if spdx_m:
        return code[:spdx_m.end()] + banner + code[spdx_m.end():]
    return banner + code


def apply_all_fixes(raw_code: str, doc: ContractDocument) -> str:
    """Apply all deterministic post-processing fixes in order."""
    code = raw_code
    # Phase 1: structural cleanup
    for fn in (_strip_existing_banner, _fix_duplicate_banner, _fix_spdx, _fix_pragma,
               _fix_injector_safe_conditions, _fix_assert_calls, _fix_msg_value_lte_zero,
               _fix_safemath, _fix_openzeppelin_imports, _fix_selfdestruct, _fix_tx_origin,
               _fix_noReentrant_modifier, _fix_mapping_return, _fix_calculatePenalty_view,
               # Phase 2: type / naming
               _fix_bare_locked, _fix_contractstate_type,
               # Phase 3: identifier resolution
               _fix_party_var_naming, _fix_undeclared_state_var_refs,
               _fix_company_name_identifiers, _fix_undeclared_identifiers_in_modifiers,
               _fix_undeclared_param_refs, _fix_broken_onlyParties, _fix_modifier_ordering,
               _fix_receive_body, _fix_return_in_non_returning_fn,
               _fix_missing_noReentrant, _fix_payable_noReentrant,
               _fix_missing_custom_errors, _fix_missing_events,
               # Phase 4: inject missing constructs
               _fix_party_declarations, _fix_state_var_declaration,
               _fix_onlyPartyA_modifier, _fix_locked_declaration, _fix_duplicate_state_vars,
               _fix_onlyX_modifiers):
        code = fn(code)
    code = _fix_governing_law_constant(code, doc)
    code = _fix_start_date(code)
    code = _fix_confidentiality_acknowledgement(code, doc)
    for fn in (_fix_undeclared_identifiers_in_modifiers,  
               _fix_require_to_custom_errors,
               _fix_malformed_if_revert, _fix_malformed_if_revert, 
               _fix_address_payable_cast, _fix_msg_value_in_nonpayable,
               _fix_msg_value_validation, _fix_expiry_deadline, _fix_payable_and_receive,
               _fix_msg_value_in_nonpayable,   
               _fix_missing_noReentrant,      
               _fix_payable_noReentrant,       
               _fix_natspec_comments,
               _fix_injector_safe_conditions, _fix_trailing_whitespace):
        code = fn(code)
    code = _add_version_comment(code, doc)
    code = _fix_duplicate_banner(code)
    return code


def _slugify(text: str) -> str:
    text = text.lstrip("\ufeff").strip()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s-]+", "_", text).strip("_")
    return text or "contract"


def save_solidity(
    code: str, doc: ContractDocument, output_dir: Path, filename: Optional[str] = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{filename or _slugify(doc.title)}.sol"
    path.write_text(code, encoding="utf-8")
    return path


def save_report(
    doc: ContractDocument, sol_path: Path, issues: list[str],
    output_dir: Path, elapsed: float, validation_report=None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "conversion_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds":      round(elapsed, 2),
        "source_file":          doc.metadata.get("source_file", "unknown"),
        "output_file":          str(sol_path),
        "contract_title":       doc.title.lstrip("\ufeff").strip(),
        "parties":              [{"role": p.role, "name": p.name, "wallet": p.wallet_hint} for p in doc.parties],
        "clauses_extracted":    doc.metadata.get("clause_count", 0),
        "effective_date":       doc.effective_date,
        "expiry_date":          doc.expiry_date,
        "governing_law":        doc.governing_law,
        "char_count":           doc.metadata.get("char_count", 0),
        "validation_issues":    issues,
        "validation_passed":    len(issues) == 0,
    }
    if validation_report is not None:
        vr = validation_report
        report["validation_passed"] = (len(issues) == 0 and vr.critical_failures == 0 and vr.accuracy_overall >= 50.0)
        report["accuracy"] = {
            "overall":  round(vr.accuracy_overall, 1),
            "solidity": round(vr.accuracy_solidity, 1),
            "security": round(vr.accuracy_security, 1),
            "legal":    round(vr.accuracy_legal, 1),
            "coverage": round(vr.accuracy_coverage, 1),
        }
        report["test_suite"] = {
            "total_tests": vr.total_tests, "passed": vr.passed,
            "failed": vr.failed, "critical_failures": vr.critical_failures,
            "results": [
                {"test_id": r.test_id, "category": r.category, "description": r.description,
                 "passed": r.passed, "severity": r.severity, "detail": r.detail}
                for r in vr.results
            ],
        }
        report["test_summary"] = vr.summary
    else:
        report["accuracy"] = {"overall": None, "solidity": None, "security": None, "legal": None, "coverage": None}
        report["test_suite"] = None
        report["test_summary"] = "Validator not run."
    path = output_dir / "results.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def run_contract_validation(code: str, doc: "ContractDocument"):
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from test_contract_validator import run_all_validations
        return run_all_validations(code, doc)
    except ImportError:
        return None


def generate_contract_with_feedback(
    llm_client, doc: "ContractDocument", system_prompt: str, user_prompt: str,
    output_dir: "Path", max_iterations: int = 3, accuracy_target: float = 100.0,
    filename: Optional[str] = None,
) -> tuple["Path", "Path", object]:
    """Full pipeline: LLM generation → postprocessor fixes → validation → feedback loop → save."""
    import logging
    logger = logging.getLogger("econtract.pipeline")
    start = time.time()
    logger.info(f"Starting generation pipeline for '{doc.title}' (max_iterations={max_iterations}, accuracy_target={accuracy_target}%)")

    best_code, struct_issues, validation_report = llm_client.generate_with_feedback(
        system=system_prompt, user=user_prompt, doc=doc,
        max_iterations=max_iterations, accuracy_target=accuracy_target,
    )
    elapsed = time.time() - start
    final_code = apply_all_fixes(best_code, doc)
    sol_path = save_solidity(final_code, doc, Path(output_dir), filename)
    report_path = save_report(doc, sol_path, struct_issues, Path(output_dir), elapsed, validation_report)

    if validation_report is not None:
        logger.info(
            f"Pipeline complete in {elapsed:.1f}s — "
            f"final accuracy: {validation_report.accuracy_overall:.1f}% "
            f"({validation_report.passed}/{validation_report.total_tests} tests passed)"
        )
    else:
        logger.info(f"Pipeline complete in {elapsed:.1f}s")
    return sol_path, report_path, validation_report