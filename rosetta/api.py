"""FastAPI service for Rosetta."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audit import audit_workbook
from .coordinator import answer as coordinator_answer
from .evaluator import Evaluator
from .graph import backward_trace, forward_impacted, forward_impacted_for_named_range
from .models import QAResponse, WhatIfImpact, WhatIfResponse
from .parser import parse_workbook
from .qa import answer  # legacy /ask endpoint still uses the regex router
from .store import chat_store, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Rosetta — Excel Intelligence Agent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    workbook_id: str
    question: str


class WhatIfRequest(BaseModel):
    workbook_id: str
    assumption: str  # named range OR cell ref
    new_value: float


class ChatRequest(BaseModel):
    workbook_id: str
    message: str
    session_id: Optional[str] = None


# --- Static frontend ---

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def root():
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {
        "service": "Rosetta",
        "version": "0.1.0",
        "endpoints": ["/ingest", "/ask", "/chat", "/trace/{workbook_id}/{cell_ref}", "/audit/{workbook_id}", "/what-if", "/workbooks"],
    }


@app.get("/api")
def api_descriptor():
    return {
        "service": "Rosetta",
        "version": "0.1.0",
        "endpoints": ["/ingest", "/ask", "/chat", "/trace/{workbook_id}/{cell_ref}", "/audit/{workbook_id}", "/what-if", "/workbooks"],
    }


@app.post("/chat")
def chat_endpoint(req: ChatRequest) -> dict[str, Any]:
    wb = store.get(req.workbook_id)
    if not wb:
        raise HTTPException(404, "workbook_id not found — ingest first")
    session = chat_store.get_or_create(req.session_id, req.workbook_id)
    return coordinator_answer(wb, session, req.message)


@app.get("/chat/{session_id}/history")
def chat_history(session_id: str) -> dict[str, Any]:
    session = chat_store.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return {
        "session_id": session.session_id,
        "workbook_id": session.workbook_id,
        "messages": [{"role": m.role, "content": m.content, "turn_id": m.turn_id}
                     for m in session.messages],
        "active_entity": session.active_entity,
        "scenario_overrides": session.scenario_overrides,
    }


class ScenarioSetRequest(BaseModel):
    overrides: dict[str, Any]


@app.post("/chat/{session_id}/scenario")
def set_scenario(session_id: str, req: ScenarioSetRequest) -> dict[str, Any]:
    session = chat_store.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    session.set_scenario(req.overrides)
    return {"session_id": session_id, "scenario_overrides": session.scenario_overrides}


@app.delete("/chat/{session_id}/scenario")
def clear_scenario(session_id: str, ref: Optional[str] = None) -> dict[str, Any]:
    session = chat_store.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    session.clear_scenario(ref)
    return {"session_id": session_id, "scenario_overrides": session.scenario_overrides}


@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    import os
    try:
        from .embeddings import is_enabled as semantic_is_enabled
        sem_available = semantic_is_enabled()
    except Exception:
        sem_available = False
    return {
        "version": "v2A",
        "workbooks_loaded": len(store.list()),
        "active_sessions": len(chat_store._sessions),  # type: ignore[attr-defined]
        "anthropic_api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "model": os.environ.get("ROSETTA_MODEL", "claude-sonnet-4-5"),
        "semantic_search_available": sem_available,
        "semantic_search_disabled_flag": os.environ.get("ROSETTA_SEMANTIC_DISABLED") == "1",
        "qdrant_url": os.environ.get("QDRANT_URL", "(embedded mode)"),
        "embedding_model": os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
    }


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx files are supported")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        wb = parse_workbook(tmp_path)
        wb.filename = file.filename
        wb.findings = audit_workbook(wb)
        store.put(wb)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # v2A: build cell contexts + embed + upsert to Qdrant (non-fatal on error)
    semantic_indexed = 0
    semantic_error: Optional[str] = None
    try:
        from .cell_context import build_cell_contexts
        from .embeddings import QdrantIndex, is_enabled
        if is_enabled():
            contexts = build_cell_contexts(wb)
            semantic_indexed = QdrantIndex.upsert_cells(wb.workbook_id, contexts)
    except Exception as e:
        semantic_error = f"{type(e).__name__}: {e}"
        logging.getLogger("rosetta.ingest").warning("Semantic index build failed: %s", e)

    return {
        "workbook_id": wb.workbook_id,
        "filename": wb.filename,
        "summary": _summarize(wb),
        "semantic_index": {
            "indexed_cells": semantic_indexed,
            "error": semantic_error,
        },
    }


def _summarize(wb) -> dict[str, Any]:
    return {
        "sheet_count": len(wb.sheets),
        "hidden_sheets": [s.name for s in wb.sheets if s.hidden],
        "total_cells": len(wb.cells),
        "formula_cells": wb.graph_summary.total_formula_cells,
        "cross_sheet_references": wb.graph_summary.cross_sheet_edges,
        "max_dependency_depth": wb.graph_summary.max_depth,
        "circular_references": [cr.model_dump() for cr in wb.graph_summary.circular_references],
        "named_ranges": [{"name": nr.name, "scope": nr.scope, "resolves_to": nr.resolved_refs,
                           "value": nr.current_value, "is_dynamic": nr.is_dynamic} for nr in wb.named_ranges],
        "sheets": [
            {
                "name": s.name, "hidden": s.hidden,
                "rows": s.max_row, "cols": s.max_col,
                "formulas": s.formula_count,
                "regions": [r.model_dump() for r in s.regions[:10]],
                "hidden_rows": s.hidden_rows, "hidden_cols": s.hidden_cols,
                "merged_cells": s.merged_cells[:10],
            } for s in wb.sheets
        ],
        "finding_counts": {cat: sum(1 for f in wb.findings if f.category == cat) for cat in
                           {f.category for f in wb.findings}},
    }


@app.post("/ask", response_model=QAResponse)
def ask(req: AskRequest) -> QAResponse:
    wb = store.get(req.workbook_id)
    if not wb:
        raise HTTPException(404, "workbook_id not found — ingest first")
    return answer(wb, req.question)


@app.get("/trace/{workbook_id}/{sheet}/{cell}")
def trace(workbook_id: str, sheet: str, cell: str) -> dict[str, Any]:
    wb = store.get(workbook_id)
    if not wb:
        raise HTTPException(404, "workbook not found")
    ref = f"{sheet}!{cell}"
    if ref not in wb.cells:
        raise HTTPException(404, f"cell {ref} not found")
    back = backward_trace(wb, ref, max_depth=8)
    fwd = forward_impacted(wb, ref)
    return {
        "cell": ref,
        "backward": back.model_dump(),
        "forward": [{"ref": r, "depth": d, "label": wb.cells[r].semantic_label if r in wb.cells else None,
                      "value": wb.cells[r].value if r in wb.cells else None} for r, d in fwd[:200]],
    }


@app.get("/audit/{workbook_id}")
def audit(workbook_id: str) -> dict[str, Any]:
    wb = store.get(workbook_id)
    if not wb:
        raise HTTPException(404, "workbook not found")
    findings = wb.findings or audit_workbook(wb)
    return {
        "workbook_id": workbook_id,
        "finding_count": len(findings),
        "findings": [f.model_dump() for f in findings],
    }


@app.post("/what-if", response_model=WhatIfResponse)
def what_if(req: WhatIfRequest) -> WhatIfResponse:
    wb = store.get(req.workbook_id)
    if not wb:
        raise HTTPException(404, "workbook not found")
    # Resolve target
    target_ref: str | None = None
    if req.assumption in wb.cells:
        target_ref = req.assumption
    else:
        nr = next((n for n in wb.named_ranges if n.name.lower() == req.assumption.lower()), None)
        if nr and nr.resolved_refs and ":" not in nr.resolved_refs[0]:
            target_ref = nr.resolved_refs[0]
    if not target_ref:
        raise HTTPException(400, f"Could not resolve assumption '{req.assumption}' to a single cell.")
    old_val = wb.cells[target_ref].value
    ev = Evaluator(wb, overrides={target_ref: req.new_value})
    # Find impacted
    impacted_refs: list[str] = []
    nr = next((n for n in wb.named_ranges if target_ref in n.resolved_refs), None)
    if nr:
        impacted_refs = [r for r, _ in forward_impacted_for_named_range(wb, nr.name)]
    else:
        impacted_refs = [r for r, _ in forward_impacted(wb, target_ref)]
    affected: list[WhatIfImpact] = []
    key: list[WhatIfImpact] = []
    key_labels = ("ebitda", "gross profit", "revenue", "performance ratio", "service absorption", "noi", "net operating income")
    for r in impacted_refs:
        new_v = ev.value_of(r)
        old_v = wb.cells[r].value
        if new_v != old_v:
            imp = WhatIfImpact(ref=r, label=wb.cells[r].semantic_label, old_value=old_v, new_value=new_v,
                              depth=0, sheet=wb.cells[r].sheet)
            affected.append(imp)
            if imp.label and any(k in imp.label.lower() for k in key_labels):
                key.append(imp)
    explanation = (f"Set {target_ref} from {old_val} to {req.new_value}. "
                   f"{len(affected)} downstream cells changed value. "
                   f"{len(ev.unsupported)} formulas were not re-evaluatable (fell back to cached values).")
    return WhatIfResponse(
        changed_input=target_ref,
        old_value=old_val,
        new_value=req.new_value,
        affected_cells=affected[:500],
        key_outputs=key[:50],
        unsupported_formulas=list(ev.unsupported)[:50],
        explanation=explanation,
        warnings=([f"{len(ev.unsupported)} formula(s) unsupported."] if ev.unsupported else []),
    )


@app.get("/workbooks")
def list_workbooks() -> dict[str, Any]:
    return {
        "workbooks": [
            {"workbook_id": wb.workbook_id, "filename": wb.filename,
             "sheets": len(wb.sheets), "cells": len(wb.cells),
             "formulas": wb.graph_summary.total_formula_cells}
            for wb in store.list()
        ]
    }


@app.get("/workbook/{workbook_id}")
def get_workbook(workbook_id: str) -> dict[str, Any]:
    wb = store.get(workbook_id)
    if not wb:
        raise HTTPException(404, "workbook not found")
    return _summarize(wb)
