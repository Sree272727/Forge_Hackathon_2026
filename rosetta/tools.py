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
        "description": "Search for cells by semantic label keyword or canonical ref. 3-tier lookup: 'exact' (canonical ref or named range name), 'keyword' (substring match on labels), 'semantic' (embedding similarity — only available if v2 is deployed; otherwise returns empty). 'auto' tries exact → keyword → semantic in order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "The search query (e.g. 'EBITDA', 'P&L Summary!G32', 'floor plan rate')."},
                "has_formula": {"type": "boolean", "description": "If true, only return cells that have a formula.", "default": False},
                "tier": {"type": "string", "description": "One of: 'auto' (default), 'exact', 'keyword', 'semantic'.", "default": "auto"},
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
    {
        "name": "get_workbook_summary",
        "description": "Return a high-level summary of the workbook: sheet names, named range count, circular refs, audit finding counts. Call this once at the start of a new question to orient yourself if you haven't already.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "scenario_recalc",
        "description": "Recompute one or more target cells with multiple input overrides applied. Unlike what_if (single target), this supports composing scenarios (e.g. FloorPlanRate=7% AND ReconCostCap=3000). Returns new values for cells that changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "overrides": {
                    "type": "object",
                    "description": "Dict mapping cell_ref_or_named_range to new value. Example: {\"FloorPlanRate\": 0.07, \"ReconCostCap\": 3000}.",
                },
                "target_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional specific cells to recompute. If omitted, recomputes all cells impacted by the overrides.",
                },
            },
            "required": ["overrides"],
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
            return _find_cells(wb, args["keyword"],
                                args.get("has_formula", False),
                                args.get("tier", "auto"))
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
        if name == "get_workbook_summary":
            return _get_workbook_summary(wb)
        if name == "scenario_recalc":
            return _scenario_recalc(wb, args["overrides"], args.get("target_refs"))
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


def _find_cells(wb: WorkbookModel, keyword: str, has_formula: bool = False,
                 tier: str = "auto") -> dict:
    """Three-tier cell lookup.

    Tiers:
      - exact: canonical cell ref like 'Sheet!A1', or exact named range name
      - keyword: substring match on cell.semantic_label (case-insensitive)
      - semantic: embedding similarity (v2 only; v1.5 returns empty stub)
      - auto: try exact → keyword → semantic until non-empty
    """
    tier = tier.lower()
    if tier not in ("auto", "exact", "keyword", "semantic"):
        return {"error": f"invalid tier: {tier}"}

    results: list[dict] = []
    tier_used: str = tier

    def _run_exact() -> list[dict]:
        out: list[dict] = []
        q = keyword.strip().replace("$", "")
        # Direct cell ref
        if q in wb.cells:
            cell = wb.cells[q]
            out.append({
                "ref": cell.ref, "label": cell.semantic_label, "value": cell.value,
                "has_formula": cell.formula is not None, "formula": cell.formula,
                "score": 1.0, "tier_used": "exact",
            })
            return out
        # Named range name
        nr = next((n for n in wb.named_ranges if n.name.lower() == q.lower()), None)
        if nr and nr.resolved_refs:
            for r in nr.resolved_refs:
                if ":" in r:
                    continue
                cell = wb.cells.get(r)
                if cell:
                    out.append({
                        "ref": cell.ref, "label": cell.semantic_label, "value": cell.value,
                        "has_formula": cell.formula is not None, "formula": cell.formula,
                        "score": 1.0, "tier_used": "exact",
                        "named_range": nr.name,
                    })
        return out

    def _run_keyword() -> list[dict]:
        out: list[dict] = []
        kw = keyword.lower().strip()
        if not kw:
            return out
        for ref, cell in wb.cells.items():
            if has_formula and not cell.formula:
                continue
            label = (cell.semantic_label or "").lower()
            if not label:
                continue
            if kw in label:
                score = 0.7 + (0.2 if cell.formula else 0)
                out.append({
                    "ref": ref, "label": cell.semantic_label, "value": cell.value,
                    "has_formula": cell.formula is not None, "formula": cell.formula,
                    "score": score, "tier_used": "keyword",
                })
            if len(out) >= 20:
                break
        # Rank: formulas first, then by label specificity
        out.sort(key=lambda m: (-m["score"], len(m["label"] or "")))
        return out

    def _run_semantic() -> list[dict]:
        # v1.5 stub — v2 Option A will implement this via Qdrant
        import os
        if os.environ.get("ROSETTA_SEMANTIC_ENABLED") != "1":
            return []
        try:
            from .embeddings import QdrantIndex  # only exists in v2
            idx = QdrantIndex()
            results = idx.search(wb.workbook_id, keyword, limit=10)
            return [
                {"ref": r["ref"], "label": r["label"], "value": None,
                 "has_formula": None, "formula": None,
                 "score": r["score"], "tier_used": "semantic"}
                for r in results if r["score"] > 0.4
            ]
        except Exception:
            return []

    if tier == "exact":
        results = _run_exact()
    elif tier == "keyword":
        results = _run_keyword()
    elif tier == "semantic":
        results = _run_semantic()
    else:  # auto
        results = _run_exact()
        if not results:
            results = _run_keyword()
            tier_used = "keyword"
        else:
            tier_used = "exact"
        if not results:
            results = _run_semantic()
            tier_used = "semantic" if results else "none"

    return {"matches": results, "count": len(results), "keyword": keyword,
             "tier_used": tier_used}


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


def _get_workbook_summary(wb: WorkbookModel) -> dict:
    from collections import Counter
    finding_counts = Counter(f.category for f in (wb.findings or []))
    return {
        "workbook_id": wb.workbook_id,
        "filename": wb.filename,
        "sheet_count": len(wb.sheets),
        "sheets": [
            {"name": s.name, "rows": s.max_row, "formulas": s.formula_count,
             "hidden": s.hidden}
            for s in wb.sheets
        ],
        "named_range_count": len(wb.named_ranges),
        "named_ranges_sample": [n.name for n in wb.named_ranges[:30]],
        "has_circular_refs": len(wb.graph_summary.circular_references) > 0,
        "circular_ref_count": len(wb.graph_summary.circular_references),
        "total_formula_cells": wb.graph_summary.total_formula_cells,
        "cross_sheet_edges": wb.graph_summary.cross_sheet_edges,
        "finding_counts": dict(finding_counts),
    }


def _scenario_recalc(wb: WorkbookModel, overrides: dict[str, Any],
                      target_refs: list[str] | None) -> dict:
    """Recompute target_refs (or all impacted) with multi-override scenario.

    overrides keys can be cell refs or named range names.
    """
    # Resolve each override key to a cell ref
    resolved: dict[str, Any] = {}
    unresolved: list[str] = []
    for key, val in overrides.items():
        ref = key.replace("$", "").strip()
        if ref in wb.cells:
            resolved[ref] = val
            continue
        # Try named range
        nr = next((n for n in wb.named_ranges if n.name.lower() == key.lower()), None)
        if nr and nr.resolved_refs and ":" not in nr.resolved_refs[0]:
            resolved[nr.resolved_refs[0]] = val
            continue
        unresolved.append(key)

    if not resolved:
        return {"error": "No overrides could be resolved to cells",
                "unresolved": unresolved}

    ev = Evaluator(wb, overrides=resolved)

    # Determine targets
    if target_refs:
        target_list = [t.replace("$", "").strip() for t in target_refs]
    else:
        # All cells impacted by any override
        impacted: set[str] = set()
        for override_ref in resolved:
            for r, _ in forward_impacted(wb, override_ref):
                impacted.add(r)
        target_list = list(impacted)

    recalculated: dict[str, Any] = {}
    unchanged_count = 0
    for r in target_list:
        cell = wb.cells.get(r)
        if not cell:
            continue
        new_v = ev.value_of(r)
        if new_v != cell.value:
            recalculated[r] = {
                "label": cell.semantic_label,
                "old": cell.value,
                "new": new_v,
            }
        else:
            unchanged_count += 1

    return {
        "overrides_applied": resolved,
        "overrides_unresolved": unresolved,
        "total_targets": len(target_list),
        "changed_count": len(recalculated),
        "unchanged_count": unchanged_count,
        "unsupported_formulas": list(ev.unsupported)[:30],
        "recalculated": recalculated,
    }
