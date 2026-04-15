"""Natural language Q&A engine.

Design:
  1. Classify question into one of 7 intents deterministically.
  2. Retrieve the relevant structured workbook objects.
  3. Build a grounded answer using only parsed data.
  4. Optionally call Anthropic Claude to polish the explanation, BUT we always
     compute the facts ourselves — the LLM never invents formulas.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from .audit import audit_workbook
from .evaluator import Evaluator, RangeArg
from .graph import backward_trace, forward_impacted, forward_impacted_for_named_range
from .models import (
    CellModel,
    QAEvidence,
    QAResponse,
    TraceNode,
    WorkbookModel,
)

log = logging.getLogger("rosetta.qa")


INTENT_PATTERNS = [
    ("what_if", [r"\bwhat if\b", r"\bif (i|we)\s+(update|change|increase|decrease|set)\b", r"\bwould (happen|change)\b", r"\bwent to\b"]),
    ("dependency", [r"\bdepend(s|ent)\b", r"\bimpact(s|ed)?\b", r"\bwould change\b", r"\bwhere is .* used\b", r"\buses?\b.*\b(assumption|named range)\b", r"\bwhere .* used\b"]),
    ("diagnostic", [r"\bstale\b", r"\bissue(s)?\b", r"\bwrong\b", r"\banomal", r"\bcircular\b", r"\bhidden\b", r"\bbroken\b", r"\baudit\b", r"\bvolatile\b", r"\bindirect\b", r"\bdeprecated\b"]),
    ("formula", [r"\bhow is\b", r"\bhow does\b", r"\bcalculated?\b", r"\bformula\b", r"\bwhat drives\b", r"\bwhat goes into\b", r"\bexplain\b", r"\badditive\b.*\bmultiplicative\b", r"\bmultiplicative\b.*\badditive\b"]),
    ("comparative", [r"\bdiffer\b", r"\bcompare\b", r"\bdifference between\b", r"\bwhich sites\b", r"\bvs\.?\b"]),
    ("cross_join", [r"\bfor each\b", r"\bcombined\b.*\bdeal\b", r"\bdeal\s*#?\s*\d+\b", r"\bjoin\b"]),
    ("value", [r"\bwhat (was|is|are)\b", r"\bhow much\b", r"\btotal\b", r"\bvalue of\b", r"\bshow me\b"]),
]


def classify(q: str) -> str:
    ql = q.lower()
    for intent, patterns in INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, ql):
                return intent
    return "value"


# --- Helpers to find cells by business name ---

CANON_ALIASES = {
    "adjusted ebitda": ["adjusted ebitda", "ebitda"],
    "total gross profit": ["total gross profit", "total gross", "gross profit"],
    "floor plan rate": ["floor plan rate", "floorplanrate", "floor plan"],
    "service absorption rate": ["service absorption", "absorption rate", "service absorption rate"],
    "performance ratio": ["performance ratio", "perf ratio", "pr"],
    "expected revenue": ["expected revenue", "forecast revenue"],
    "actual revenue": ["actual revenue", "ytd revenue"],
    "system loss": ["system loss", "total system loss", "loss"],
    "degradation": ["degradation", "annualdegradationrate"],
    "net operating income": ["net operating income", "noi"],
    "tax rate": ["tax rate", "taxrate"],
    "fi pvr": ["fi pvr", "f&i pvr", "pvr"],
}


def _find_named_range(wb: WorkbookModel, q: str) -> str | None:
    ql = q.lower()
    # Exact-name match first
    for nr in wb.named_ranges:
        nm = nr.name.lower()
        if nm in ql:
            return nr.name
        # Camel-case split lookup
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", nr.name).lower()
        if spaced in ql:
            return nr.name
    # Alias match
    for canon, aliases in CANON_ALIASES.items():
        for a in aliases:
            if a in ql:
                # Look for a named range whose name contains the canonical bits
                key = canon.replace(" ", "").lower()
                for nr in wb.named_ranges:
                    if nr.name.lower().replace(" ", "") == key:
                        return nr.name
    return None


def _find_metric_cell(wb: WorkbookModel, q: str) -> CellModel | None:
    """Find the cell whose semantic_label matches a metric name in the question."""
    ql = q.lower()
    # Explicit cell ref like P&L Summary!G32 or G32
    m = re.search(r"([A-Za-z_][\w ]*)!([A-Z]{1,3}\d+)", q)
    if m:
        ref = f"{m.group(1).strip()}!{m.group(2)}"
        if ref in wb.cells:
            return wb.cells[ref]

    candidates: list[tuple[int, CellModel]] = []
    # Resolve metric name
    targets: list[str] = []
    for canon, aliases in CANON_ALIASES.items():
        for a in aliases:
            if a in ql:
                targets.append(canon)
                break
    if not targets:
        # Use any quoted or capitalized phrase
        m = re.search(r'"([^"]+)"', q)
        if m:
            targets.append(m.group(1).lower())

    # Extract possible row specifier (e.g. "Site Alpha", "Deal 1042")
    row_keyword = None
    row_m = re.search(r"\b(?:site|row|for)\s+([A-Za-z][A-Za-z0-9_ ]{1,30}?)(?:[,.\?\)]|\s+in|$)", q, re.IGNORECASE)
    if row_m:
        row_keyword = row_m.group(1).strip().lower().rstrip()
    # Also try site proper names inline
    if not row_keyword:
        for site in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa"):
            if re.search(rf"\b{site}\b", q.lower()):
                row_keyword = site
                break

    for ref, cell in wb.cells.items():
        if not cell.semantic_label or not cell.formula:
            continue
        label_l = cell.semantic_label.lower()
        for t in targets:
            if t in label_l:
                score = (10 if cell.formula_type and "cross" in cell.formula_type else 0)
                if row_keyword and row_keyword in label_l:
                    score += 1000
                elif row_keyword and row_keyword not in label_l:
                    score -= 5
                # small tie-break for more-specific labels
                score += min(len(cell.depends_on), 5)
                candidates.append((score, cell))
                break

    if not candidates:
        # fallback: label-only match for any labeled cell (even without formula)
        for ref, cell in wb.cells.items():
            if not cell.semantic_label:
                continue
            label_l = cell.semantic_label.lower()
            for t in targets:
                if t in label_l:
                    candidates.append((0, cell))
                    break

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def _find_month_column(wb: WorkbookModel, sheet_name: str, month: str) -> str | None:
    """Find the column in a sheet whose header row contains the given month."""
    month_l = month.lower()
    month_aliases = {
        "january": ["january", "jan"], "february": ["february", "feb"],
        "march": ["march", "mar"], "april": ["april", "apr"], "may": ["may"],
        "june": ["june", "jun"], "july": ["july", "jul"], "august": ["august", "aug"],
        "september": ["september", "sep", "sept"], "october": ["october", "oct"],
        "november": ["november", "nov"], "december": ["december", "dec"],
    }
    target_aliases = None
    for k, v in month_aliases.items():
        if month_l in v or k.startswith(month_l):
            target_aliases = v
            break
    if not target_aliases:
        return None
    for ref, cell in wb.cells.items():
        if not ref.startswith(f"{sheet_name}!"):
            continue
        if not isinstance(cell.value, str):
            continue
        if cell.value.strip().lower() in target_aliases:
            return "".join(ch for ch in cell.coord if ch.isalpha())
    return None


# --- Answer builders ---

def _explain_trace(trace: TraceNode, wb: WorkbookModel, depth_limit: int = 3) -> str:
    """Render a backward trace in plain business language."""
    lines: list[str] = []
    def render(n: TraceNode, indent: int):
        if indent > depth_limit:
            return
        pad = "  " * indent
        label = f" ({n.label})" if n.label else ""
        val_repr = _fmt(n.value)
        nr = f" [named range: {n.named_range}]" if n.named_range else ""
        marker = ""
        if n.is_hardcoded:
            marker = " [hardcoded]"
        elif n.is_volatile:
            marker = " [volatile]"
        if n.formula:
            lines.append(f"{pad}- {n.ref}{label} = {val_repr}{nr}{marker}")
            lines.append(f"{pad}    formula: ={n.formula}")
        else:
            lines.append(f"{pad}- {n.ref}{label} = {val_repr}{nr}{marker}")
        for w in n.warnings:
            lines.append(f"{pad}    ⚠ {w}")
        for c in n.children:
            render(c, indent + 1)
    render(trace, 0)
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _business_explanation(cell: CellModel, wb: WorkbookModel) -> str:
    """One-paragraph business explanation of a formula cell."""
    if not cell.formula:
        return f"{cell.ref} holds a fixed value of {_fmt(cell.value)}."
    parts: list[str] = []
    # Introduce
    label = cell.semantic_label or cell.ref
    parts.append(f"{label} is computed in {cell.ref} as =\u200B{cell.formula}.")
    # Components
    comp_parts: list[str] = []
    for d in cell.depends_on:
        if ":" in d.split("!", 1)[-1]:
            comp_parts.append(f"the range {d}")
            continue
        dc = wb.cells.get(d)
        if not dc:
            comp_parts.append(d)
            continue
        lbl = dc.semantic_label or d
        comp_parts.append(f"{lbl} at {d} (={_fmt(dc.value)})")
    if comp_parts:
        parts.append("It depends on " + "; ".join(comp_parts[:6]) + ("." if len(comp_parts) <= 6 else f"; and {len(comp_parts)-6} more references."))
    # Named ranges
    if cell.named_ranges_used:
        nr_strs = []
        for nm in cell.named_ranges_used:
            nr = next((n for n in wb.named_ranges if n.name == nm), None)
            if nr:
                nr_strs.append(f"{nm} → {', '.join(nr.resolved_refs)} (current value {_fmt(nr.current_value)})")
        if nr_strs:
            parts.append("Named ranges resolved: " + "; ".join(nr_strs) + ".")
    # Warnings
    if cell.is_volatile:
        parts.append("Note: this formula is volatile — recalculates on every workbook change.")
    return " ".join(parts)


# --- Intent handlers ---

def _answer_value(wb: WorkbookModel, q: str) -> QAResponse:
    # Try month + metric cell
    cell = _find_metric_cell(wb, q)
    if cell:
        # Try to find month
        m = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", q.lower())
        if m:
            col = _find_month_column(wb, cell.sheet, m.group(1))
            if col:
                row = int("".join(ch for ch in cell.coord if ch.isdigit()))
                ref = f"{cell.sheet}!{col}{row}"
                target = wb.cells.get(ref) or cell
                return QAResponse(
                    question=q, answer_type="value",
                    answer=f"{cell.semantic_label or ref} for {m.group(1).capitalize()} = {_fmt(target.value)} (cell {ref}).",
                    evidence=[QAEvidence(ref=target.ref, label=target.semantic_label, value=target.value, formula=target.formula)],
                    confidence=0.9,
                )
        return QAResponse(
            question=q, answer_type="value",
            answer=f"{cell.semantic_label or cell.ref} = {_fmt(cell.value)} (cell {cell.ref}).",
            evidence=[QAEvidence(ref=cell.ref, label=cell.semantic_label, value=cell.value, formula=cell.formula)],
            confidence=0.85,
        )
    # Named range lookup
    nr_name = _find_named_range(wb, q)
    if nr_name:
        nr = next(n for n in wb.named_ranges if n.name == nr_name)
        return QAResponse(
            question=q, answer_type="value",
            answer=f"Named range '{nr.name}' resolves to {', '.join(nr.resolved_refs)} with current value {_fmt(nr.current_value)}.",
            evidence=[QAEvidence(ref=r, value=wb.cells.get(r).value if r in wb.cells else None) for r in nr.resolved_refs],
            confidence=0.95,
        )
    return QAResponse(question=q, answer_type="value",
                     answer="I couldn't find a metric matching that description. Try naming the metric (e.g. 'Adjusted EBITDA', 'Performance Ratio'), including a cell reference (Sheet!A1), or a named range.",
                     warnings=["no match found"], confidence=0.2)


def _answer_formula(wb: WorkbookModel, q: str) -> QAResponse:
    cell = _find_metric_cell(wb, q)
    nr_name = _find_named_range(wb, q) if not cell else None
    if not cell and nr_name:
        nr = next(n for n in wb.named_ranges if n.name == nr_name)
        if nr.resolved_refs and nr.resolved_refs[0] in wb.cells:
            cell = wb.cells[nr.resolved_refs[0]]
    if not cell:
        return QAResponse(question=q, answer_type="formula",
                         answer="I couldn't identify the target metric to explain. Try naming the metric explicitly or giving a cell reference.",
                         warnings=["no target identified"], confidence=0.2)
    if not cell.formula:
        return QAResponse(question=q, answer_type="formula",
                         answer=f"{cell.semantic_label or cell.ref} is a hardcoded value ({_fmt(cell.value)}), not computed from a formula.",
                         evidence=[QAEvidence(ref=cell.ref, label=cell.semantic_label, value=cell.value)],
                         confidence=0.95)
    trace = backward_trace(wb, cell.ref, max_depth=6)
    explanation = _business_explanation(cell, wb)
    trace_text = _explain_trace(trace, wb, depth_limit=3)
    warnings: list[str] = []
    if cell.is_volatile:
        warnings.append("volatile formula — may recalculate unexpectedly.")
    for d in cell.depends_on:
        dc = wb.cells.get(d)
        if dc and dc.is_hardcoded:
            warnings.append(f"{d} is a hardcoded input (value={_fmt(dc.value)}).")
    return QAResponse(
        question=q, answer_type="formula",
        answer=f"{explanation}\n\nDependency trace:\n{trace_text}",
        evidence=[QAEvidence(ref=cell.ref, label=cell.semantic_label, value=cell.value, formula=cell.formula)],
        trace=trace,
        warnings=warnings,
        confidence=0.92,
    )


def _answer_dependency(wb: WorkbookModel, q: str) -> QAResponse:
    nr_name = _find_named_range(wb, q)
    cell = None
    if not nr_name:
        cell = _find_metric_cell(wb, q)
    if nr_name:
        impacted = forward_impacted_for_named_range(wb, nr_name)
        nr = next(n for n in wb.named_ranges if n.name == nr_name)
        # Group by sheet
        by_sheet: dict[str, list[tuple[str, int]]] = {}
        for ref, depth in impacted:
            sh = ref.split("!", 1)[0]
            by_sheet.setdefault(sh, []).append((ref, depth))
        lines = [f"Named range '{nr.name}' resolves to {', '.join(nr.resolved_refs)} (current value {_fmt(nr.current_value)})."]
        lines.append(f"If it changes, {len(impacted)} downstream formula cells are affected:")
        for sh, items in sorted(by_sheet.items()):
            sample = items[:5]
            lines.append(f"  • {sh}: {len(items)} cells. Examples: " + ", ".join(ref for ref, _ in sample))
        # Highlight key outputs (known metric labels)
        key_refs = [ref for ref, _ in impacted if wb.cells.get(ref) and wb.cells[ref].semantic_label
                    and any(m in (wb.cells[ref].semantic_label or "").lower()
                            for m in ("ebitda", "gross profit", "revenue", "performance ratio",
                                     "service absorption", "noi", "net income"))]
        if key_refs:
            lines.append("Key business outputs affected: " + ", ".join(key_refs[:8]))
        return QAResponse(
            question=q, answer_type="dependency",
            answer="\n".join(lines),
            evidence=[QAEvidence(ref=r, label=wb.cells[r].semantic_label if r in wb.cells else None,
                                 value=wb.cells[r].value if r in wb.cells else None) for r, _ in impacted[:10]],
            confidence=0.9,
        )
    if cell:
        impacted = forward_impacted(wb, cell.ref)
        by_sheet: dict[str, list[str]] = {}
        for ref, _ in impacted:
            by_sheet.setdefault(ref.split("!", 1)[0], []).append(ref)
        lines = [f"{cell.semantic_label or cell.ref} ({cell.ref}) feeds into {len(impacted)} downstream formula cells:"]
        for sh, items in sorted(by_sheet.items()):
            lines.append(f"  • {sh}: {len(items)} cells. Examples: " + ", ".join(items[:5]))
        return QAResponse(question=q, answer_type="dependency", answer="\n".join(lines),
                         evidence=[QAEvidence(ref=cell.ref, label=cell.semantic_label, value=cell.value, formula=cell.formula)],
                         confidence=0.9)
    return QAResponse(question=q, answer_type="dependency",
                     answer="I couldn't identify the assumption or cell whose impact you want to trace.",
                     warnings=["no target found"], confidence=0.2)


def _answer_diagnostic(wb: WorkbookModel, q: str) -> QAResponse:
    findings = wb.findings or audit_workbook(wb)
    ql = q.lower()
    # Filter by keyword
    if "stale" in ql:
        findings = [f for f in findings if f.category == "stale_assumption"]
    elif "circular" in ql:
        findings = [f for f in findings if f.category == "circular"]
    elif "hidden" in ql:
        findings = [f for f in findings if f.category == "hidden_dependency"]
    elif "hardcode" in ql or "different" in ql or "anomal" in ql:
        findings = [f for f in findings if f.category == "hardcoded_anomaly"]
    elif "volatile" in ql or "indirect" in ql:
        findings = [f for f in findings if f.category in ("volatile", "hidden_dependency")]
    if not findings:
        return QAResponse(question=q, answer_type="diagnostic",
                         answer="No matching diagnostic findings in this workbook.",
                         confidence=0.85)
    lines = [f"Found {len(findings)} finding(s):"]
    for f in findings[:20]:
        lines.append(f"  [{f.severity.upper()}] {f.category} @ {f.location or '—'}: {f.message}")
    return QAResponse(question=q, answer_type="diagnostic", answer="\n".join(lines),
                     evidence=[QAEvidence(ref=f.location or "—", label=f.category, value=f.message) for f in findings[:10]],
                     warnings=[f"{f.category}" for f in findings if f.severity == "error"],
                     confidence=0.9)


def _answer_what_if(wb: WorkbookModel, q: str) -> QAResponse:
    # Extract target and new value from question
    nr_name = _find_named_range(wb, q)
    # Parse number (supports %)
    num_m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(%)?", q)
    if not nr_name or not num_m:
        return QAResponse(question=q, answer_type="what_if",
                         answer="I couldn't pin down the assumption or target value. Try a wording like: 'What would change if FloorPlanRate went to 6.5%?'",
                         warnings=["could not parse inputs"], confidence=0.3)
    new_val = float(num_m.group(1))
    if num_m.group(2) == "%":
        new_val /= 100
    nr = next(n for n in wb.named_ranges if n.name == nr_name)
    if not nr.resolved_refs or ":" in nr.resolved_refs[0]:
        return QAResponse(question=q, answer_type="what_if",
                         answer=f"Named range '{nr.name}' doesn't resolve to a single cell (got {nr.resolved_refs}); what-if requires a scalar assumption cell.",
                         warnings=["non-scalar named range"], confidence=0.4)
    target_ref = nr.resolved_refs[0]
    target_cell = wb.cells.get(target_ref)
    old_val = target_cell.value if target_cell else None
    ev = Evaluator(wb, overrides={target_ref: new_val})
    # Recompute impacted cells
    impacted_refs = [r for r, _ in forward_impacted_for_named_range(wb, nr.name)]
    changes: list[dict[str, Any]] = []
    for r in impacted_refs:
        old = wb.cells[r].value if r in wb.cells else None
        new = ev.value_of(r)
        if old != new:
            changes.append({"ref": r, "old": old, "new": new,
                            "label": wb.cells[r].semantic_label if r in wb.cells else None})
    # Key outputs
    key_labels = ("ebitda", "gross profit", "revenue", "performance ratio",
                  "service absorption", "noi", "net operating income")
    key = [c for c in changes if c["label"] and any(k in c["label"].lower() for k in key_labels)]
    lines = [f"If {nr.name} changes from {_fmt(old_val)} to {_fmt(new_val)}:"]
    if not changes:
        lines.append("  No downstream cells changed (likely because evaluator couldn't recompute unsupported formulas).")
    else:
        lines.append(f"  {len(changes)} cells have new values.")
        for c in key[:10]:
            lines.append(f"  • {c['label'] or c['ref']} ({c['ref']}): {_fmt(c['old'])} → {_fmt(c['new'])} (Δ {_fmt((c['new'] or 0) - (c['old'] or 0))})")
        other = [c for c in changes if c not in key][:10]
        if other:
            lines.append("  Other changed cells:")
            for c in other:
                lines.append(f"    - {c['ref']}: {_fmt(c['old'])} → {_fmt(c['new'])}")
    warnings = []
    if ev.unsupported:
        warnings.append(f"{len(ev.unsupported)} formulas could not be recomputed (unsupported functions).")
    return QAResponse(question=q, answer_type="what_if", answer="\n".join(lines),
                     evidence=[QAEvidence(ref=c["ref"], label=c["label"], value=c["new"]) for c in key[:5]],
                     warnings=warnings, confidence=0.8)


def _answer_cross_join(wb: WorkbookModel, q: str) -> QAResponse:
    # Simple deal# extractor
    m = re.search(r"deal\s*#?\s*(\d+)", q.lower())
    if not m:
        return QAResponse(question=q, answer_type="cross_join",
                         answer="For cross-sheet joins I need a key like a Deal# or Site name.",
                         warnings=["no join key"], confidence=0.3)
    deal_id = int(m.group(1))
    # Find cells containing this deal number
    matches: list[tuple[str, Any]] = []
    for ref, cell in wb.cells.items():
        if isinstance(cell.value, (int, float)) and int(cell.value) == deal_id:
            # Check row context
            sheet = cell.sheet
            row = int("".join(ch for ch in cell.coord if ch.isdigit()))
            header_refs = [r for r in wb.cells if r.startswith(f"{sheet}!") and r.endswith(f"!{''.join(ch for ch in cell.coord if ch.isalpha())}1")]
            header = wb.cells[header_refs[0]].value if header_refs else None
            matches.append((sheet, row, cell.ref, header))
    if not matches:
        return QAResponse(question=q, answer_type="cross_join",
                         answer=f"No rows found referencing Deal #{deal_id}.", confidence=0.4)
    lines = [f"Deal #{deal_id} appears in:"]
    evid: list[QAEvidence] = []
    for sheet, row, ref, header in matches:
        # Gather row cells
        row_cells = [c for r, c in wb.cells.items() if r.startswith(f"{sheet}!") and r.endswith(str(row)) and "".join(ch for ch in c.coord if ch.isdigit()) == str(row)]
        summary = ", ".join(f"{c.semantic_label or c.coord}={_fmt(c.value)}" for c in row_cells[:8] if c.value is not None)
        lines.append(f"  • {sheet} row {row}: {summary}")
        evid.append(QAEvidence(ref=ref, label=sheet, value=deal_id))
    return QAResponse(question=q, answer_type="cross_join", answer="\n".join(lines), evidence=evid, confidence=0.75)


def _answer_comparative(wb: WorkbookModel, q: str) -> QAResponse:
    # Compare sheet structures
    lines = ["Sheet structural comparison:"]
    for sheet in wb.sheets:
        formula_types: dict[str, int] = {}
        for r in sheet.cell_refs:
            c = wb.cells.get(r)
            if c and c.formula:
                formula_types[c.formula_type or "arithmetic"] = formula_types.get(c.formula_type or "arithmetic", 0) + 1
        lines.append(f"  • {sheet.name}: {sheet.formula_count} formulas — " +
                     ", ".join(f"{k}:{v}" for k, v in formula_types.items()))
    return QAResponse(question=q, answer_type="comparative", answer="\n".join(lines), confidence=0.7)


def answer(wb: WorkbookModel, q: str) -> QAResponse:
    intent = classify(q)
    log.info("Question=%r intent=%s", q, intent)
    handlers = {
        "value": _answer_value,
        "formula": _answer_formula,
        "dependency": _answer_dependency,
        "diagnostic": _answer_diagnostic,
        "what_if": _answer_what_if,
        "cross_join": _answer_cross_join,
        "comparative": _answer_comparative,
    }
    handler = handlers.get(intent, _answer_value)
    resp = handler(wb, q)
    # Optional LLM polish
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            resp.answer = _polish_with_llm(q, resp, intent)
        except Exception as e:
            log.warning("LLM polish failed: %s", e)
    return resp


def _polish_with_llm(q: str, resp: QAResponse, intent: str) -> str:
    """Use Claude to rewrite the grounded answer in clearer business language,
    but without inventing any new facts. All facts come from resp.
    """
    try:
        import anthropic  # type: ignore
    except ImportError:
        return resp.answer
    client = anthropic.Anthropic()
    prompt = f"""You are Rosetta, an Excel intelligence agent. Rewrite the grounded answer below in clearer business language. Do not invent any facts or references. Keep every cell reference, value, and warning. Keep the answer concise.

Question: {q}
Intent: {intent}
Grounded answer:
---
{resp.answer}
---

Return only the polished answer."""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    if msg.content and msg.content[0].type == "text":
        return msg.content[0].text.strip()
    return resp.answer
