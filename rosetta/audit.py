"""Diagnostic / audit engine — finds issues in the parsed workbook."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .models import AuditFinding, WorkbookModel


def _is_date(v: Any) -> bool:
    return hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day")


def audit_workbook(wb: WorkbookModel) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    findings.extend(_stale_assumptions(wb))
    findings.extend(_hidden_deps(wb))
    findings.extend(_volatile_formulas(wb))
    findings.extend(_hardcoded_anomalies(wb))
    findings.extend(_circular_references(wb))
    findings.extend(_broken_refs(wb))
    return findings


def _stale_assumptions(wb: WorkbookModel) -> list[AuditFinding]:
    """Look for rows that look like assumption entries with dates older than 12 months."""
    findings: list[AuditFinding] = []
    cutoff = datetime.utcnow() - timedelta(days=365)
    # Heuristic: for each sheet whose name contains 'assumption', scan rows for date columns
    for sheet in wb.sheets:
        name_lower = sheet.name.lower()
        looks_like_assumptions = "assumption" in name_lower or "settings" in name_lower
        if not looks_like_assumptions:
            continue
        for r in range(1, sheet.max_row + 1):
            label_ref = f"{sheet.name}!A{r}"
            label = wb.cells.get(label_ref)
            if not label or not isinstance(label.value, str):
                continue
            # Scan columns for a date value
            for c_letter in ["B", "C", "D", "E"]:
                ref = f"{sheet.name}!{c_letter}{r}"
                c = wb.cells.get(ref)
                if c and _is_date(c.value):
                    try:
                        dt = datetime(c.value.year, c.value.month, c.value.day)
                    except Exception:
                        continue
                    if dt < cutoff:
                        findings.append(
                            AuditFinding(
                                severity="warning",
                                category="stale_assumption",
                                location=label_ref,
                                message=f"Assumption '{label.value}' last updated {dt.date().isoformat()} — more than 12 months old.",
                                detail={"assumption": label.value, "date": dt.date().isoformat()},
                                confidence=0.9,
                            )
                        )
                    break
    return findings


def _hidden_deps(wb: WorkbookModel) -> list[AuditFinding]:
    """Flag formulas that reference hidden sheets or hidden rows."""
    findings: list[AuditFinding] = []
    hidden_sheets = {s.name for s in wb.sheets if s.hidden}
    hidden_rows_by_sheet: dict[str, set[int]] = {s.name: set(s.hidden_rows) for s in wb.sheets}
    for ref, cell in wb.cells.items():
        if not cell.formula:
            continue
        for d in cell.depends_on:
            if "!" not in d:
                continue
            ds, dc = d.split("!", 1)
            if ds in hidden_sheets:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        category="hidden_dependency",
                        location=ref,
                        message=f"{ref} depends on {d} which is on hidden sheet '{ds}'.",
                        confidence=0.95,
                    )
                )
                continue
            # hidden row
            if ":" in dc:
                continue
            row_m = "".join(ch for ch in dc if ch.isdigit())
            if row_m and int(row_m) in hidden_rows_by_sheet.get(ds, set()):
                findings.append(
                    AuditFinding(
                        severity="info",
                        category="hidden_dependency",
                        location=ref,
                        message=f"{ref} depends on {d} which is in a hidden row.",
                        confidence=0.85,
                    )
                )
    return findings


def _volatile_formulas(wb: WorkbookModel) -> list[AuditFinding]:
    out: list[AuditFinding] = []
    for ref, cell in wb.cells.items():
        if cell.is_volatile:
            out.append(
                AuditFinding(
                    severity="info",
                    category="volatile",
                    location=ref,
                    message=f"{ref} uses a volatile function — result may change on recalculation even without input changes. Formula: ={cell.formula}",
                    confidence=0.95,
                )
            )
    return out


def _hardcoded_anomalies(wb: WorkbookModel) -> list[AuditFinding]:
    """Find rows where most neighbors have formulas but this cell is hardcoded."""
    findings: list[AuditFinding] = []
    # Group cells by (sheet, column)
    col_cells: dict[tuple[str, str], list[int]] = defaultdict(list)
    for ref, cell in wb.cells.items():
        col = "".join(ch for ch in cell.coord if ch.isalpha())
        row = int("".join(ch for ch in cell.coord if ch.isdigit()))
        col_cells[(cell.sheet, col)].append(row)

    for (sheet, col), rows in col_cells.items():
        rows.sort()
        if len(rows) < 5:
            continue
        formula_rows = []
        hard_rows = []
        for r in rows:
            cell = wb.cells.get(f"{sheet}!{col}{r}")
            if not cell:
                continue
            if cell.formula:
                formula_rows.append(r)
            elif isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                hard_rows.append(r)
        if len(formula_rows) >= 5 and len(hard_rows) > 0 and len(hard_rows) <= len(formula_rows) // 3:
            # Only flag hardcoded rows that are interior (between formula rows)
            f_set = set(formula_rows)
            for hr in hard_rows:
                above = any(fr < hr for fr in f_set)
                below = any(fr > hr for fr in f_set)
                if above and below:
                    findings.append(
                        AuditFinding(
                            severity="warning",
                            category="hardcoded_anomaly",
                            location=f"{sheet}!{col}{hr}",
                            message=f"{sheet}!{col}{hr} is a hardcoded number where {len(formula_rows)} neighboring rows use formulas. Possible manual override.",
                            detail={"value": wb.cells[f"{sheet}!{col}{hr}"].value,
                                    "neighboring_formula_sample": wb.cells[f"{sheet}!{col}{formula_rows[0]}"].formula},
                            confidence=0.75,
                        )
                    )
    return findings


def _circular_references(wb: WorkbookModel) -> list[AuditFinding]:
    out: list[AuditFinding] = []
    for cr in wb.graph_summary.circular_references:
        out.append(
            AuditFinding(
                severity="info" if cr.intentional else "error",
                category="circular",
                location=cr.chain[0] if cr.chain else None,
                message=f"Circular reference detected: {' → '.join(cr.chain)}. {cr.note or ''}",
                detail={"chain": cr.chain, "intentional": cr.intentional},
                confidence=0.95,
            )
        )
    return out


def _broken_refs(wb: WorkbookModel) -> list[AuditFinding]:
    out: list[AuditFinding] = []
    for ref, cell in wb.cells.items():
        if isinstance(cell.value, str) and cell.value.startswith("#REF"):
            out.append(
                AuditFinding(
                    severity="error",
                    category="broken_ref",
                    location=ref,
                    message=f"{ref} contains a broken reference error: {cell.value}.",
                    confidence=1.0,
                )
            )
    return out
