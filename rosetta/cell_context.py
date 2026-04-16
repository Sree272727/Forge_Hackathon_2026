"""Rich cell-context builder for semantic indexing.

For each labeled cell in a workbook, produce a context_string that
captures enough business meaning for embedding-based retrieval:
  "<sheet> / <row_header> / <col_header> / <formula_type> / <flags>"

Example:
  "P&L Summary / Adjusted EBITDA / Mar 2026 / cross_sheet_calculation / major_output"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import WorkbookModel


@dataclass
class CellContext:
    ref: str
    sheet: str
    coord: str
    semantic_label: Optional[str]
    row_header: Optional[str]
    col_header: Optional[str]
    formula_type: Optional[str]
    is_summary_cell: bool
    is_major_output: bool
    context_string: str


def _coord_parts(coord: str) -> tuple[str, int]:
    """Split 'G32' → ('G', 32)."""
    col = "".join(ch for ch in coord if ch.isalpha())
    row_digits = "".join(ch for ch in coord if ch.isdigit())
    row = int(row_digits) if row_digits else 0
    return col, row


def _nearest_row_header(wb: WorkbookModel, sheet: str, row: int) -> Optional[str]:
    """Column A string value at (row) is the common 'row header'."""
    ref = f"{sheet}!A{row}"
    c = wb.cells.get(ref)
    if c and isinstance(c.value, str):
        return c.value
    return None


def _nearest_col_header(wb: WorkbookModel, sheet: str, col: str) -> Optional[str]:
    """Try rows 1-3 for the first string value in this column."""
    for r in (1, 2, 3):
        ref = f"{sheet}!{col}{r}"
        c = wb.cells.get(ref)
        if c and isinstance(c.value, str):
            return c.value
    return None


def _is_in_subtotal_region(wb: WorkbookModel, sheet_name: str, row: int) -> bool:
    sheet = next((s for s in wb.sheets if s.name == sheet_name), None)
    if not sheet:
        return False
    for region in sheet.regions:
        start, end = region.rows
        if start <= row <= end and region.type in ("subtotal", "summary"):
            return True
    return False


def _is_major_output(wb: WorkbookModel, cell) -> bool:
    """Heuristic: business-sounding label + has formula + dep tree depth >= 2."""
    if not cell.formula or not cell.semantic_label:
        return False
    label_low = cell.semantic_label.lower()
    business_terms = (
        "ebitda", "gross profit", "net income", "revenue", "expense",
        "total", "operating income", "noi", "net operating", "absorption",
        "performance ratio", "margin", "yield", "return",
    )
    if not any(t in label_low for t in business_terms):
        return False
    # Depth proxy: has >= 2 dependencies OR some deps are themselves formulas
    if len(cell.depends_on) >= 2:
        return True
    for dep_ref in cell.depends_on:
        dc = wb.cells.get(dep_ref)
        if dc and dc.formula:
            return True
    return False


def build_cell_contexts(wb: WorkbookModel) -> list[CellContext]:
    """Build a CellContext for every cell that has a label or a formula.

    We skip cells that are purely blank or without semantic meaning.
    """
    contexts: list[CellContext] = []
    for ref, cell in wb.cells.items():
        # Only index cells with business meaning
        has_label = bool(cell.semantic_label)
        has_formula = cell.formula is not None
        if not has_label and not has_formula:
            continue

        col, row = _coord_parts(cell.coord)
        row_header = _nearest_row_header(wb, cell.sheet, row) if row > 0 else None
        col_header = _nearest_col_header(wb, cell.sheet, col)
        summary = _is_in_subtotal_region(wb, cell.sheet, row)
        major = _is_major_output(wb, cell)

        context_parts = [
            cell.sheet,
            row_header,
            col_header,
            cell.formula_type if cell.formula else "input",
            "summary" if summary else None,
            "major_output" if major else None,
        ]
        context_str = " / ".join(p for p in context_parts if p)

        contexts.append(CellContext(
            ref=ref,
            sheet=cell.sheet,
            coord=cell.coord,
            semantic_label=cell.semantic_label,
            row_header=row_header,
            col_header=col_header,
            formula_type=cell.formula_type,
            is_summary_cell=summary,
            is_major_output=major,
            context_string=context_str,
        ))

    return contexts
