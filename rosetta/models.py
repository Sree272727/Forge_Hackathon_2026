"""Pydantic data models for Rosetta's internal workbook representation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


CellRef = str  # "Sheet!A1" canonical form


class CellModel(BaseModel):
    """Single cell with value + formula + dependency info."""
    sheet: str
    coord: str  # e.g. "G32"
    ref: CellRef  # canonical "Sheet!G32"
    value: Any = None
    formula: Optional[str] = None  # raw formula string (no leading =)
    formula_type: Optional[str] = None  # aggregation | lookup | conditional | arithmetic | cross_sheet_sum | ...
    depends_on: list[CellRef] = Field(default_factory=list)
    depended_by: list[CellRef] = Field(default_factory=list)
    named_ranges_used: list[str] = Field(default_factory=list)
    is_hardcoded: bool = False
    is_volatile: bool = False
    data_type: Optional[str] = None  # 'number' | 'string' | 'date' | 'bool' | 'error'
    semantic_label: Optional[str] = None  # nearest row/column header label


class NamedRangeModel(BaseModel):
    name: str
    scope: str  # 'workbook' or sheet name
    raw_value: str  # the formula/reference stored in the defined name
    resolved_refs: list[CellRef] = Field(default_factory=list)
    current_value: Any = None
    is_dynamic: bool = False  # uses OFFSET/INDIRECT/etc.


class RegionModel(BaseModel):
    type: Literal["header", "data", "subtotal", "calculation", "blank", "summary"]
    rows: tuple[int, int]
    columns: Optional[tuple[str, str]] = None
    note: Optional[str] = None


class SheetModel(BaseModel):
    name: str
    hidden: bool = False
    max_row: int = 0
    max_col: int = 0
    merged_cells: list[str] = Field(default_factory=list)
    hidden_rows: list[int] = Field(default_factory=list)
    hidden_cols: list[str] = Field(default_factory=list)
    regions: list[RegionModel] = Field(default_factory=list)
    formula_count: int = 0
    cell_refs: list[CellRef] = Field(default_factory=list)  # only cells with values or formulas


class CircularRef(BaseModel):
    chain: list[CellRef]
    intentional: bool = False
    note: Optional[str] = None


class AuditFinding(BaseModel):
    severity: Literal["info", "warning", "error"]
    category: str  # 'stale_assumption' | 'hidden_dependency' | 'hardcoded_anomaly' | 'circular' | 'volatile' | 'broken_ref' | 'inconsistency'
    location: Optional[CellRef] = None
    message: str
    detail: Optional[dict[str, Any]] = None
    confidence: float = 0.9


class DependencyGraphSummary(BaseModel):
    total_formula_cells: int
    max_depth: int
    cross_sheet_edges: int
    circular_references: list[CircularRef] = Field(default_factory=list)


class WorkbookModel(BaseModel):
    workbook_id: str
    filename: str
    sheets: list[SheetModel]
    named_ranges: list[NamedRangeModel]
    # Flat cell store keyed by canonical ref
    cells: dict[CellRef, CellModel]
    graph_summary: DependencyGraphSummary
    findings: list[AuditFinding] = Field(default_factory=list)
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


# --- Response models ---

class TraceNode(BaseModel):
    ref: CellRef
    label: Optional[str] = None
    value: Any = None
    formula: Optional[str] = None
    depth: int = 0
    is_hardcoded: bool = False
    is_volatile: bool = False
    named_range: Optional[str] = None
    children: list["TraceNode"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QAEvidence(BaseModel):
    ref: CellRef
    label: Optional[str] = None
    value: Any = None
    formula: Optional[str] = None


class QAResponse(BaseModel):
    question: str
    answer: str
    answer_type: str  # value | formula | dependency | diagnostic | comparative | what_if | cross_join
    evidence: list[QAEvidence] = Field(default_factory=list)
    trace: Optional[TraceNode] = None
    warnings: list[str] = Field(default_factory=list)
    confidence: float = 0.85


class WhatIfImpact(BaseModel):
    ref: CellRef
    label: Optional[str] = None
    old_value: Any = None
    new_value: Any = None
    depth: int = 0
    sheet: str


class WhatIfResponse(BaseModel):
    changed_input: CellRef
    old_value: Any
    new_value: Any
    affected_cells: list[WhatIfImpact]
    key_outputs: list[WhatIfImpact]
    unsupported_formulas: list[CellRef] = Field(default_factory=list)
    explanation: str
    warnings: list[str] = Field(default_factory=list)


TraceNode.model_rebuild()
