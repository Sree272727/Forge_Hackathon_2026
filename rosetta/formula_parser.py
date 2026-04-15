"""Deterministic extraction of cell references from Excel formulas.

We do not build a full Excel AST. We extract:
  - same-sheet refs (A1, $A$1, A1:B5)
  - cross-sheet refs (Sheet1!A1, 'Sheet 1'!A1, 'Sheet 1'!$A$1:$C$10)
  - named range usages (identifiers not matching refs)
  - function names (for volatile / formula-type detection)

The reference extractor is token-based: we iterate the formula character by
character, tracking string literals, quoted sheet names, and refs. This is
good enough for all demo workbook formulas in the spec and most real
workbooks we'll encounter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

VOLATILE_FUNCS = {"NOW", "TODAY", "RAND", "RANDBETWEEN", "OFFSET", "INDIRECT", "INFO", "CELL"}

AGGREGATION_FUNCS = {"SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "PRODUCT", "SUMPRODUCT",
                      "SUMIF", "SUMIFS", "AVERAGEIF", "AVERAGEIFS", "COUNTIF", "COUNTIFS"}
LOOKUP_FUNCS = {"VLOOKUP", "HLOOKUP", "XLOOKUP", "INDEX", "MATCH", "LOOKUP", "FILTER", "CHOOSE"}
CONDITIONAL_FUNCS = {"IF", "IFS", "IFERROR", "IFNA", "SWITCH", "AND", "OR", "NOT"}

# Match a cell ref: optional $ before col and row. Col = 1-3 letters, row = 1-7 digits.
CELL_RE = re.compile(r"(?<![A-Za-z0-9_])\$?([A-Z]{1,3})\$?(\d{1,7})")
RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_])\$?([A-Z]{1,3})\$?(\d{1,7}):\$?([A-Z]{1,3})\$?(\d{1,7})"
)
# Whole-column ref like A:A, $A:$B
WHOLE_COL_RE = re.compile(r"(?<![A-Za-z0-9_])\$?([A-Z]{1,3}):\$?([A-Z]{1,3})(?!\d)")
FUNC_RE = re.compile(r"\b([A-Z][A-Z0-9_\.]+)\s*\(")
# identifier that isn't followed by ( and isn't a cell ref pattern
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*")


@dataclass
class ParsedFormula:
    raw: str
    refs: list[str] = field(default_factory=list)  # canonical "Sheet!A1" or "Sheet!A1:B5"
    named_ranges: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    is_volatile: bool = False
    formula_type: str = "arithmetic"
    cross_sheet: bool = False


def col_to_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n


def index_to_col(idx: int) -> str:
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def canonical_ref(sheet: str, coord: str) -> str:
    # Strip $ signs
    coord = coord.replace("$", "")
    return f"{sheet}!{coord}"


def expand_range(sheet: str, start: str, end: str, max_row: int = 1048576, max_col: int = 16384) -> list[str]:
    """Expand a range like A1:B3 to individual canonical refs.

    For whole-column refs we cap at max_row.
    """
    m1 = re.match(r"\$?([A-Z]{1,3})\$?(\d+)?", start)
    m2 = re.match(r"\$?([A-Z]{1,3})\$?(\d+)?", end)
    if not m1 or not m2:
        return []
    c1, r1 = m1.group(1), m1.group(2)
    c2, r2 = m2.group(1), m2.group(2)
    c1i, c2i = sorted([col_to_index(c1), col_to_index(c2)])
    if r1 is None or r2 is None:
        # whole column — leave as a single logical edge rather than expand
        return [f"{sheet}!{index_to_col(c1i)}:{index_to_col(c2i)}"]
    r1i, r2i = sorted([int(r1), int(r2)])
    if (c2i - c1i + 1) * (r2i - r1i + 1) > 5000:
        # too big — represent as logical range
        return [f"{sheet}!{index_to_col(c1i)}{r1i}:{index_to_col(c2i)}{r2i}"]
    out = []
    for col in range(c1i, c2i + 1):
        for row in range(r1i, r2i + 1):
            out.append(f"{sheet}!{index_to_col(col)}{row}")
    return out


def _tokenize_refs(formula: str, default_sheet: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Yield (kind, payload) tokens for refs. kind in {cell, range, whole_col, sheet_prefix}.

    Returns (ref_tokens, raw_identifiers_seen) where ref_tokens is a list of
    (sheet, coord_or_range) already resolved to a sheet name. Identifiers are
    potential named ranges — handled after we subtract cell-ref-looking tokens.
    """
    i = 0
    n = len(formula)
    refs: list[tuple[str, str]] = []
    identifiers: list[str] = []

    while i < n:
        ch = formula[i]
        # Skip string literals "..."
        if ch == '"':
            j = i + 1
            while j < n:
                if formula[j] == '"':
                    if j + 1 < n and formula[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue

        # Quoted sheet name 'Sheet Name'!Ref or 'Sheet Name'!A1:B2
        if ch == "'":
            j = i + 1
            while j < n:
                if formula[j] == "'":
                    if j + 1 < n and formula[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            sheet_name = formula[i + 1 : j]
            i = j + 1
            if i < n and formula[i] == "!":
                i += 1
                ref_str, consumed = _consume_ref_after_sheet(formula, i)
                if ref_str:
                    refs.append((sheet_name, ref_str))
                i += consumed
                continue
            continue

        # Unquoted sheet reference like Sheet1!A1
        m = re.match(r"([A-Za-z_][A-Za-z0-9_\.]*)!", formula[i:])
        if m:
            sheet_name = m.group(1)
            i += m.end()
            ref_str, consumed = _consume_ref_after_sheet(formula, i)
            if ref_str:
                refs.append((sheet_name, ref_str))
                i += consumed
            continue

        # Try a cell ref or range in default sheet
        m_range = RANGE_RE.match(formula, i)
        if m_range:
            start = f"{m_range.group(1)}{m_range.group(2)}"
            end = f"{m_range.group(3)}{m_range.group(4)}"
            refs.append((default_sheet, f"{start}:{end}"))
            i = m_range.end()
            continue

        m_col = WHOLE_COL_RE.match(formula, i)
        if m_col:
            refs.append((default_sheet, f"{m_col.group(1)}:{m_col.group(2)}"))
            i = m_col.end()
            continue

        m_cell = CELL_RE.match(formula, i)
        if m_cell:
            refs.append((default_sheet, f"{m_cell.group(1)}{m_cell.group(2)}"))
            i = m_cell.end()
            continue

        # Identifier — could be function name or named range
        m_id = IDENT_RE.match(formula, i)
        if m_id:
            ident = m_id.group(0)
            end = m_id.end()
            # skip if immediately followed by ( — it's a function
            after = end
            while after < n and formula[after] == " ":
                after += 1
            if after < n and formula[after] == "(":
                i = end
                continue
            identifiers.append(ident)
            i = end
            continue

        i += 1

    return refs, identifiers


def _consume_ref_after_sheet(formula: str, i: int) -> tuple[str, int]:
    """After 'Sheet!' starting at i, consume A1 / A1:B2 / A:A / $A$1."""
    # Try range first
    m_range = RANGE_RE.match(formula, i)
    if m_range and m_range.start() == i:
        return f"{m_range.group(1)}{m_range.group(2)}:{m_range.group(3)}{m_range.group(4)}", m_range.end() - i
    m_col = WHOLE_COL_RE.match(formula, i)
    if m_col and m_col.start() == i:
        return f"{m_col.group(1)}:{m_col.group(2)}", m_col.end() - i
    m_cell = CELL_RE.match(formula, i)
    if m_cell and m_cell.start() == i:
        return f"{m_cell.group(1)}{m_cell.group(2)}", m_cell.end() - i
    return "", 0


def parse_formula(
    formula: str,
    default_sheet: str,
    named_range_names: Iterable[str] | None = None,
) -> ParsedFormula:
    """Parse a formula and extract references + metadata."""
    if formula is None:
        return ParsedFormula(raw="")
    raw = formula
    if raw.startswith("="):
        raw = raw[1:]

    pf = ParsedFormula(raw=formula)

    # Function detection
    for m in FUNC_RE.finditer(raw):
        fn = m.group(1).upper()
        pf.functions.append(fn)
        if fn in VOLATILE_FUNCS:
            pf.is_volatile = True

    # Reference extraction
    ref_tokens, idents = _tokenize_refs(raw, default_sheet)
    nr_set = {n.upper() for n in (named_range_names or [])}
    # Filter identifiers: keep those matching a known named range
    for ident in idents:
        if ident.upper() in nr_set and ident not in pf.named_ranges:
            pf.named_ranges.append(ident)

    canonical: list[str] = []
    for sheet, ref in ref_tokens:
        if sheet != default_sheet:
            pf.cross_sheet = True
        if ":" in ref:
            # range — keep as logical range ref
            canonical.append(f"{sheet}!{ref.replace('$', '')}")
        else:
            canonical.append(f"{sheet}!{ref.replace('$', '')}")
    # Dedup preserving order
    seen = set()
    dedup = []
    for r in canonical:
        if r not in seen:
            seen.add(r)
            dedup.append(r)
    pf.refs = dedup

    # Formula-type classification
    fset = {f for f in pf.functions}
    if pf.cross_sheet and fset & AGGREGATION_FUNCS:
        pf.formula_type = "cross_sheet_aggregation"
    elif pf.cross_sheet:
        pf.formula_type = "cross_sheet"
    elif fset & LOOKUP_FUNCS:
        pf.formula_type = "lookup"
    elif fset & CONDITIONAL_FUNCS and fset & AGGREGATION_FUNCS:
        pf.formula_type = "conditional_aggregation"
    elif fset & CONDITIONAL_FUNCS:
        pf.formula_type = "conditional"
    elif fset & AGGREGATION_FUNCS:
        pf.formula_type = "aggregation"
    else:
        pf.formula_type = "arithmetic"

    return pf


def expand_refs(refs: list[str]) -> list[str]:
    """Expand any range refs in the list to individual cells where feasible."""
    out: list[str] = []
    for r in refs:
        if "!" not in r:
            out.append(r)
            continue
        sheet, rng = r.split("!", 1)
        if ":" in rng:
            start, end = rng.split(":", 1)
            out.extend(expand_range(sheet, start, end))
        else:
            out.append(r)
    # Dedup
    seen = set()
    dedup = []
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup
