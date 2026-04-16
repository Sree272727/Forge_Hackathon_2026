"""Citation auditor — ensures no hallucinated numbers, cell refs, or named ranges.

Spec: docs/plan_v1_5.md §9.

Rules (condensed):
  - Every numeric value (currency, percentage, plain number) in the answer must
    match within floating-point tolerance to some value that appeared in a
    tool result or in wb.cells.
  - Every cell reference (Sheet!Ref) must match something a tool returned or
    a known cell in the workbook.
  - Every named-range name must be in wb.named_ranges.
  - Qualitative keywords (stale, circular, hidden, volatile, hardcoded) require
    at least one matching AuditFinding category to have been surfaced this
    session.
  - Zero values (0, 0%, $0) are always allowed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .conversation import ToolCall
from .models import WorkbookModel


# --- Patterns ---

# Numbers: optional $, optional sign, digit groups, optional decimal, optional %
NUMBER_RE = re.compile(
    r"(?<![A-Za-z_\-/])\$?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"   # with commas
    r"|(?<![A-Za-z_\-/])\$?-?\d+(?:\.\d+)?%?"                 # plain
)

# Date patterns — masked out before number extraction to avoid parsing YYYY/MM/DD
DATE_RE = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"                        # 2023-01-15
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"                     # 1/15/2023 or 01/15/23
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{2,4}\b"  # Jan 15, 2023
)

# Cell ref with quoted sheet: 'Sheet Name'!A1
CELL_REF_QUOTED_RE = re.compile(
    r"'([^']+)'!(\$?[A-Z]{1,3}\$?\d+)"
)

# Cell ref with simple unquoted sheet: SheetName!A1 (no spaces)
CELL_REF_SIMPLE_RE = re.compile(
    r"(?<![\w!])([A-Za-z_]\w*)!(\$?[A-Z]{1,3}\$?\d+)"
)

# Coordinate-only pattern (used for prose-embedded multi-word sheet names
# like `... in P&L Summary!G32`): find each `!CELLREF` and walk backward.
COORD_RE = re.compile(r"!(\$?[A-Z]{1,3}\$?\d+)\b")

# Qualitative keywords that require audit-finding backing
QUAL_KEYWORDS = {
    "stale": "stale_assumption",
    "circular": "circular",
    "hidden": "hidden_dependency",
    "volatile": "volatile",
    "hardcoded": "hardcoded_anomaly",
    "hard-coded": "hardcoded_anomaly",
    "deprecated": "stale_assumption",
    "broken": "broken_ref",
}


@dataclass
class AuditResult:
    status: str  # "passed" | "failed"
    violations: list[str] = field(default_factory=list)
    verified_numbers: list[str] = field(default_factory=list)
    verified_refs: list[str] = field(default_factory=list)
    verified_named_ranges: list[str] = field(default_factory=list)
    verified_qualitative: list[str] = field(default_factory=list)


# --- Parsing helpers ---

def _parse_number(token: str) -> Optional[tuple[float, bool]]:
    """Parse '$1,234.56%' → (12.3456, True). Returns (value, was_percent).

    Returns None if token doesn't parse cleanly.
    """
    t = token.strip()
    was_percent = t.endswith("%")
    if was_percent:
        t = t[:-1]
    t = t.lstrip("$").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    if was_percent:
        # "5.8%" → also compare against 0.058
        return (v / 100.0, True)
    return (v, False)


def _mask_dates(text: str) -> str:
    """Replace date patterns with placeholders so they don't get parsed as numbers."""
    return DATE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _extract_numbers(text: str) -> list[tuple[str, float, bool]]:
    """Return list of (raw_token, parsed_value, is_percent). Dates are masked first."""
    masked = _mask_dates(text)
    out: list[tuple[str, float, bool]] = []
    for m in NUMBER_RE.finditer(masked):
        raw = m.group(0)
        parsed = _parse_number(raw)
        if parsed is None:
            continue
        out.append((raw, parsed[0], parsed[1]))
    return out


def _extract_cell_refs(text: str, known_sheets: set[str]) -> list[str]:
    """Return canonical refs present in text.

    Strategy — pass once per `!CELLREF` occurrence, preferring longest match:
      1. Quoted 'Sheet Name'!A1 → direct
      2. Multi-word sheet ending just before `!` matching a known_sheets entry
      3. Simple unquoted single-word `Foo!A1` (fallback — validates against
         known_sheets if provided, else returns as-is)
      4. Anything else → "?UNKNOWN_SHEET!COORD" violation marker
    """
    refs: list[str] = []
    handled_bang_positions: set[int] = set()
    known_sorted = sorted(known_sheets, key=len, reverse=True) if known_sheets else []

    # 1. Quoted sheet refs
    for m in CELL_REF_QUOTED_RE.finditer(text):
        sheet = m.group(1)
        coord = m.group(2).replace("$", "")
        refs.append(f"{sheet}!{coord}")
        # Record bang position
        bang_rel = m.group(0).index("!")
        handled_bang_positions.add(m.start() + bang_rel)

    # For remaining `!COORD`, try multi-word, then simple, else flag
    for m in COORD_RE.finditer(text):
        bang_pos = m.start()
        if bang_pos in handled_bang_positions:
            continue
        coord = m.group(1).replace("$", "")

        # Try multi-word known-sheet match (longest first)
        left = text[max(0, bang_pos - 80):bang_pos]
        matched_sheet = None
        for sheet_name in known_sorted:
            if not left.endswith(sheet_name):
                continue
            prev_idx = bang_pos - len(sheet_name) - 1
            # The character before the sheet name must not be part of another identifier
            if prev_idx < 0 or not text[prev_idx].isalnum():
                matched_sheet = sheet_name
                break
        if matched_sheet:
            refs.append(f"{matched_sheet}!{coord}")
            handled_bang_positions.add(bang_pos)
            continue

        # Fall back to simple single-word identifier immediately before `!`
        simple_m = re.search(r"([A-Za-z_]\w*)$", left)
        if simple_m:
            sheet = simple_m.group(1)
            refs.append(f"{sheet}!{coord}")
            handled_bang_positions.add(bang_pos)
            continue

        # Couldn't resolve a sheet → flag
        refs.append(f"?UNKNOWN_SHEET!{coord}")
        handled_bang_positions.add(bang_pos)

    return refs


def _extract_named_ranges(text: str, known_names: set[str]) -> list[str]:
    """Return occurrences of known named range names in text (case-sensitive whole-word)."""
    hits: list[str] = []
    for name in known_names:
        # word boundary is tricky for camelCase; just check substring presence
        # but only whole words / not part of a longer identifier
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
        if pattern.search(text):
            hits.append(name)
    return hits


# --- Value universe construction ---

def _collect_values_from_tools(tool_log: list[ToolCall]) -> tuple[set[float], set[str], set[str]]:
    """Return (numeric_universe, ref_universe, finding_categories_seen)."""
    nums: set[float] = set()
    refs: set[str] = set()
    cats: set[str] = set()

    def _walk(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and ("ref" in k.lower() or k.lower() == "ref"):
                    if isinstance(v, str) and "!" in v:
                        refs.add(v.replace("$", ""))
                if k == "category" and isinstance(v, str):
                    cats.add(v)
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            nums.add(float(obj))
        elif isinstance(obj, str):
            # strings may be "Sheet!A1" refs
            if "!" in obj:
                m = CELL_REF_QUOTED_RE.match(obj) or CELL_REF_SIMPLE_RE.match(obj)
                if m:
                    sheet = m.group(1)
                    coord = m.group(2).replace("$", "")
                    refs.add(f"{sheet}!{coord}")
            # or a numeric-looking string
            parsed = _parse_number(obj)
            if parsed is not None:
                nums.add(parsed[0])

    for tc in tool_log:
        _walk(tc.output)
    return nums, refs, cats


def _collect_workbook_universe(wb: WorkbookModel) -> tuple[set[float], set[str], set[str]]:
    nums: set[float] = set()
    refs: set[str] = set()
    nr_names: set[str] = set()

    for ref, cell in wb.cells.items():
        refs.add(ref)
        if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
            nums.add(float(cell.value))

    for nr in wb.named_ranges:
        nr_names.add(nr.name)
        if isinstance(nr.current_value, (int, float)) and not isinstance(nr.current_value, bool):
            nums.add(float(nr.current_value))

    return nums, refs, nr_names


# --- Matching ---

TOLERANCE_RELATIVE = 0.005   # 0.5%
TOLERANCE_ABSOLUTE = 0.5     # fallback for small numbers / rounded display


def _number_matches(target: float, universe: Iterable[float]) -> bool:
    for v in universe:
        if target == v:
            return True
        if v == 0:
            if abs(target) < TOLERANCE_ABSOLUTE:
                return True
            continue
        if abs(target - v) / abs(v) <= TOLERANCE_RELATIVE:
            return True
        if abs(target - v) <= TOLERANCE_ABSOLUTE:
            return True
        # Also allow rounded display ($487,500 when actual is $487,532)
        if abs(target - round(v, -3)) <= 500:
            return True
    return False


def _ref_matches(target: str, universe: set[str]) -> bool:
    target_clean = target.replace("$", "")
    if target_clean in universe:
        return True
    # Case-insensitive sheet match as fallback
    target_lower = target_clean.lower()
    for v in universe:
        if v.lower() == target_lower:
            return True
    return False


# --- Public API ---

def audit(answer_text: str,
          tool_log: list[ToolCall],
          wb: WorkbookModel) -> AuditResult:
    """Verify every claim in the answer is grounded in tool results or the workbook."""
    result = AuditResult(status="passed")

    tool_nums, tool_refs, seen_categories = _collect_values_from_tools(tool_log)
    wb_nums, wb_refs, nr_names = _collect_workbook_universe(wb)

    # Universes: tool-returned takes precedence, workbook is fallback
    num_universe = tool_nums | wb_nums
    ref_universe = tool_refs | wb_refs

    # Findings categories that have been surfaced this session via list_findings
    qualitative_universe = seen_categories
    # Also, if wb.findings has any of these categories, they're latently available
    for f in wb.findings or []:
        qualitative_universe.add(f.category)

    # --- Numbers ---
    for raw, val, is_pct in _extract_numbers(answer_text):
        # Allow zero unconditionally
        if val == 0:
            result.verified_numbers.append(raw)
            continue
        # Try the value as-is
        if _number_matches(val, num_universe):
            result.verified_numbers.append(raw)
            continue
        # For percentages, also try the raw (e.g. "5.8%" might match 5.8 literal)
        if is_pct:
            raw_pct_val = val * 100
            if _number_matches(raw_pct_val, num_universe):
                result.verified_numbers.append(raw)
                continue
        result.violations.append(f"Unverified number: {raw}")

    # --- Cell refs ---
    known_sheets = {s.name for s in wb.sheets}
    for ref in _extract_cell_refs(answer_text, known_sheets):
        if ref.startswith("?UNKNOWN_SHEET!"):
            result.violations.append(
                f"Cell reference with unrecognized sheet: ...{ref.replace('?UNKNOWN_SHEET!', '!')}"
            )
            continue
        if _ref_matches(ref, ref_universe):
            result.verified_refs.append(ref)
        else:
            result.violations.append(f"Unverified cell reference: {ref}")

    # --- Named ranges ---
    for name in _extract_named_ranges(answer_text, nr_names):
        result.verified_named_ranges.append(name)
    # Detect identifier-looking words that LOOK like named ranges but aren't known
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]{3,})\b", answer_text):
        candidate = m.group(1)
        if candidate in nr_names:
            continue
        # Skip common English words in TitleCase that appear in prose
        if candidate.lower() in {"adjusted", "total", "gross", "profit", "loss", "summary",
                                  "vehicle", "used", "service", "parts", "march", "january",
                                  "february", "april", "may", "june", "july", "august",
                                  "september", "october", "november", "december",
                                  "expected", "actual", "revenue", "cost", "income",
                                  "assumptions", "operating", "detail", "floor", "plan",
                                  "rate", "ebitda", "body", "shop", "note", "noi"}:
            continue
        # Skip if it looks like part of a sheet ref (already handled)
        # Looks like CamelCase / snake_case identifier — possibly fabricated
        if re.search(r"[a-z][A-Z]|_", candidate):
            # Likely an identifier — if not a known named range, flag
            result.violations.append(f"Unknown identifier (possibly fabricated named range): {candidate}")

    # --- Qualitative ---
    low = answer_text.lower()
    for kw, category in QUAL_KEYWORDS.items():
        if kw in low:
            if category in qualitative_universe:
                result.verified_qualitative.append(f"{kw} → {category}")
            else:
                result.violations.append(
                    f"Qualitative claim '{kw}' not backed by any audit finding"
                )

    if result.violations:
        result.status = "failed"
    return result


def format_violations_for_retry(violations: list[str]) -> str:
    """Produce the retry-prompt fragment for the coordinator."""
    lines = ["Your previous answer contained unverified claims:"]
    for v in violations[:20]:
        lines.append(f"  - {v}")
    lines.append("")
    lines.append("Either remove these claims, or call a tool that returns them, then regenerate.")
    return "\n".join(lines)
