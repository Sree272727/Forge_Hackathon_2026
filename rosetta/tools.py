"""Claude tool-calling interface over the parsed WorkbookModel.

Every tool is a pure function that reads from the parsed workbook. The LLM
calls these tools to ground its answer — it never invents formulas or refs.
"""
from __future__ import annotations

from typing import Any

from .evaluator import Evaluator
from .graph import backward_trace, forward_impacted, forward_impacted_for_named_range
from .models import WorkbookModel


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_sheets",
        "description": "List every sheet in the workbook with row/column counts, formula counts, hidden status, and structural regions. Call this first to orient yourself.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_named_ranges",
        "description": "List every named range (workbook- or sheet-scoped) with its resolved cell reference and current value. Named ranges carry business meaning (e.g. FloorPlanRate).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_cell",
        "description": "Get the value, formula, dependencies, and semantic label of a specific cell. Use canonical form 'Sheet!A1' (no dollar signs).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Canonical cell ref like 'P&L Summary!G32'"}
            },
            "required": ["ref"],
        },
    },
    {
        "name": "find_cells",
        "description": "Search for cells by semantic label keyword (e.g. 'EBITDA', 'gross profit', 'Site Alpha'). Returns up to 20 candidate cells with their refs, labels, values, and formula presence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "A substring to match against cell semantic labels (case-insensitive)."},
                "has_formula": {"type": "boolean", "description": "If true, only return cells that have a formula.", "default": False},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "backward_trace",
        "description": "Return the full backward dependency tree for a cell — everything that feeds into it, recursively. Use this to answer 'how is X calculated?' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Canonical cell ref like 'P&L Summary!G32'"},
                "max_depth": {"type": "integer", "description": "How deep to traverse. Default 6.", "default": 6},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "forward_impact",
        "description": "Return every cell downstream of the given cell (what would change if this cell changed). Use for dependency/impact questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Canonical cell ref."},
                "max_results": {"type": "integer", "default": 100},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "resolve_named_range",
        "description": "Look up a single named range by name. Returns the target ref, current value, and whether it's dynamic (OFFSET/INDIRECT).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_findings",
        "description": "Return audit findings: stale assumptions, hardcoded anomalies, circular references, volatile formulas, hidden dependencies, broken refs. Optionally filter by category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional filter: stale_assumption | hardcoded_anomaly | circular | volatile | hidden_dependency | broken_ref | inconsistency",
                }
            },
            "required": [],
        },
    },
    {
        "name": "what_if",
        "description": "Recompute the workbook with a single input changed. Pass either a named range name OR a cell ref as 'target'. Returns the list of cells whose value changed and by how much.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Named range name (e.g. 'FloorPlanRate') or cell ref (e.g. 'Assumptions!B2')."},
                "new_value": {"type": "number"},
                "max_results": {"type": "integer", "default": 30},
            },
            "required": ["target", "new_value"],
        },
    },
]


# --- Executor ---

def execute_tool(wb: WorkbookModel, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "list_sheets":
            return _list_sheets(wb)
        if name == "list_named_ranges":
            return _list_named_ranges(wb)
        if name == "get_cell":
            return _get_cell(wb, args["ref"])
        if name == "find_cells":
            return _find_cells(wb, args["keyword"], args.get("has_formula", False))
        if name == "backward_trace":
            return _backward_trace(wb, args["ref"], int(args.get("max_depth", 6)))
        if name == "forward_impact":
            return _forward_impact(wb, args["ref"], int(args.get("max_results", 100)))
        if name == "resolve_named_range":
            return _resolve_named_range(wb, args["name"])
        if name == "list_findings":
            return _list_findings(wb, args.get("category"))
        if name == "what_if":
            return _what_if(wb, args["target"], float(args["new_value"]), int(args.get("max_results", 30)))
        return {"error": f"unknown tool: {name}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _list_sheets(wb: WorkbookModel) -> dict:
    return {
        "sheets": [
            {
                "name": s.name,
                "hidden": s.hidden,
                "rows": s.max_row,
                "cols": s.max_col,
                "formulas": s.formula_count,
                "regions": [{"type": r.type, "rows": list(r.rows)} for r in s.regions[:10]],
                "hidden_rows": s.hidden_rows,
                "hidden_cols": s.hidden_cols,
            }
            for s in wb.sheets
        ]
    }


def _list_named_ranges(wb: WorkbookModel) -> dict:
    return {
        "named_ranges": [
            {
                "name": nr.name,
                "scope": nr.scope,
                "resolves_to": nr.resolved_refs,
                "current_value": nr.current_value,
                "is_dynamic": nr.is_dynamic,
            }
            for nr in wb.named_ranges
        ]
    }


def _get_cell(wb: WorkbookModel, ref: str) -> dict:
    ref = ref.replace("$", "").strip()
    cell = wb.cells.get(ref)
    if not cell:
        return {"error": f"cell not found: {ref}"}
    return {
        "ref": cell.ref,
        "sheet": cell.sheet,
        "coord": cell.coord,
        "value": cell.value,
        "formula": cell.formula,
        "formula_type": cell.formula_type,
        "semantic_label": cell.semantic_label,
        "depends_on": cell.depends_on[:50],
        "depended_by": cell.depended_by[:50],
        "named_ranges_used": cell.named_ranges_used,
        "is_hardcoded": cell.is_hardcoded,
        "is_volatile": cell.is_volatile,
    }


def _find_cells(wb: WorkbookModel, keyword: str, has_formula: bool) -> dict:
    kw = keyword.lower().strip()
    matches: list[dict] = []
    for ref, cell in wb.cells.items():
        if has_formula and not cell.formula:
            continue
        label = (cell.semantic_label or "").lower()
        if not label:
            continue
        if kw in label:
            matches.append({
                "ref": ref,
                "label": cell.semantic_label,
                "value": cell.value,
                "has_formula": cell.formula is not None,
                "formula": cell.formula,
            })
        if len(matches) >= 20:
            break
    return {"matches": matches, "count": len(matches), "keyword": keyword}


def _backward_trace(wb: WorkbookModel, ref: str, max_depth: int) -> dict:
    ref = ref.replace("$", "").strip()
    if ref not in wb.cells:
        return {"error": f"cell not found: {ref}"}
    trace = backward_trace(wb, ref, max_depth=max_depth)
    return {"trace": trace.model_dump()}


def _forward_impact(wb: WorkbookModel, ref: str, max_results: int) -> dict:
    ref = ref.replace("$", "").strip()
    if ref not in wb.cells:
        return {"error": f"cell not found: {ref}"}
    impacted = forward_impacted(wb, ref)
    by_sheet: dict[str, list[dict]] = {}
    for r, depth in impacted[:max_results]:
        cell = wb.cells.get(r)
        sheet = r.split("!", 1)[0]
        by_sheet.setdefault(sheet, []).append({
            "ref": r, "depth": depth,
            "label": cell.semantic_label if cell else None,
            "value": cell.value if cell else None,
        })
    return {
        "total_impacted": len(impacted),
        "returned": min(len(impacted), max_results),
        "by_sheet": by_sheet,
    }


def _resolve_named_range(wb: WorkbookModel, name: str) -> dict:
    nr = next((n for n in wb.named_ranges if n.name.lower() == name.lower()), None)
    if not nr:
        return {"error": f"named range not found: {name}"}
    return {
        "name": nr.name,
        "scope": nr.scope,
        "resolves_to": nr.resolved_refs,
        "current_value": nr.current_value,
        "is_dynamic": nr.is_dynamic,
        "raw": nr.raw_value,
    }


def _list_findings(wb: WorkbookModel, category: str | None) -> dict:
    findings = wb.findings or []
    if category:
        findings = [f for f in findings if f.category == category]
    return {
        "count": len(findings),
        "findings": [
            {
                "severity": f.severity,
                "category": f.category,
                "location": f.location,
                "message": f.message,
                "confidence": f.confidence,
                "detail": f.detail,
            }
            for f in findings[:50]
        ],
    }


def _what_if(wb: WorkbookModel, target: str, new_value: float, max_results: int) -> dict:
    # Resolve target to a cell ref
    target_clean = target.replace("$", "").strip()
    target_ref: str | None = None
    nr_name: str | None = None
    if target_clean in wb.cells:
        target_ref = target_clean
    else:
        nr = next((n for n in wb.named_ranges if n.name.lower() == target.lower()), None)
        if nr and nr.resolved_refs and ":" not in nr.resolved_refs[0]:
            target_ref = nr.resolved_refs[0]
            nr_name = nr.name
    if not target_ref:
        return {"error": f"could not resolve target '{target}' to a scalar cell or named range"}
    old_val = wb.cells[target_ref].value
    ev = Evaluator(wb, overrides={target_ref: new_value})
    if nr_name:
        impacted = [r for r, _ in forward_impacted_for_named_range(wb, nr_name)]
    else:
        impacted = [r for r, _ in forward_impacted(wb, target_ref)]
    changes: list[dict] = []
    for r in impacted:
        cell = wb.cells.get(r)
        if not cell:
            continue
        new_v = ev.value_of(r)
        if new_v != cell.value:
            delta = None
            try:
                if isinstance(new_v, (int, float)) and isinstance(cell.value, (int, float)):
                    delta = new_v - cell.value
            except Exception:
                pass
            changes.append({
                "ref": r,
                "label": cell.semantic_label,
                "old": cell.value,
                "new": new_v,
                "delta": delta,
            })
    # Sort: cells with business labels first, then by absolute delta
    def sort_key(c):
        has_label = 1 if c.get("label") else 0
        d = abs(c["delta"]) if isinstance(c.get("delta"), (int, float)) else 0
        return (-has_label, -d)
    changes.sort(key=sort_key)
    return {
        "target": target_ref,
        "named_range": nr_name,
        "old_value": old_val,
        "new_value": new_value,
        "total_changed": len(changes),
        "unsupported_formulas": len(ev.unsupported),
        "changes": changes[:max_results],
    }
