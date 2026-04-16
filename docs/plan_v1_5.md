# Rosetta v1.5 — Execution Plan

**Status:** APPROVED for implementation
**Target:** 10–12 hours of focused work
**Owner context:** This document is self-contained. A fresh Claude session should be able to execute from this file alone without reading other conversation history.

---

## 1. What v1.5 is (one paragraph)

Replace the current regex-based Q&A router (`rosetta/qa.py`) with a proper agentic coordinator. The coordinator plans each question, calls deterministic tools over the parsed workbook, optionally delegates to a FormulaExplainer specialist, and passes every answer through a **citation auditor** that guarantees no hallucinated numbers. Conversational memory, scenario overrides, and an answer cache live in a `ConversationState` object. All of this uses the **existing** parser, graph, audit engine, custom evaluator, and tool palette — no new external dependencies.

## 2. What v1.5 is NOT (explicit non-goals)

The following are out of scope for v1.5 and belong to v2 (see `docs/plan_v2_upgrades.md`):

- ❌ Qdrant vector database
- ❌ Semantic cell search / embeddings
- ❌ `formulas` pip package / new evaluator
- ❌ StructuralComparator specialist (fold into coordinator for now)
- ❌ `compute` tool (coordinator may do simple arithmetic; auditor verifies result)
- ❌ `list_pivots` tool
- ❌ `compare_sheet_structure` tool
- ❌ `version_diff` tool
- ❌ Workbook versioning with hash invalidation
- ❌ Deleting `rosetta/evaluator.py` (keep, marked deprecated candidate)
- ❌ Async ingest pipeline / job queue
- ❌ Multi-tenancy / auth
- ❌ LibreOffice fallback

Keep `rosetta/qa.py` and `rosetta/evaluator.py` in the repo, but:
- Do not import `qa.answer()` from `coordinator.py` or `chat.py`
- Add a module-level docstring to both: `"""DEPRECATED — superseded by rosetta/coordinator.py in v1.5. Retained for reference and possible reuse of CANON_ALIASES, _find_metric_cell, etc."""`

---

## 3. Target architecture (v1.5 slice)

```
┌─────────────────────────────────────────────────┐
│                 INGEST PIPELINE                  │
│  .xlsx → parse_workbook → audit → store         │
│  (unchanged from v1)                             │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│            CONVERSATION STATE                    │
│  • workbook_id                                   │
│  • messages (full history)                       │
│  • active_entity (last ref/metric)               │
│  • scenario_overrides: dict[ref, value]          │
│  • answer_cache: dict[q_hash, CachedAnswer]      │
│  • tool_call_log: list[ToolCall]                 │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│              COORDINATOR AGENT                   │
│        Claude Sonnet 4.5, temp=0                 │
│        tools = DETERMINISTIC_TOOLS (9 existing)  │
│        system prompt = COORDINATOR_PROMPT        │
└─────┬─────────────────────────────────────┬─────┘
      │ tool calls                           │ delegate
      ▼                                      ▼
┌─────────────────────┐         ┌──────────────────────────┐
│ DETERMINISTIC TOOLS │         │ FormulaExplainer          │
│  (existing in       │         │  Claude Sonnet 4.5         │
│   rosetta/tools.py) │         │  in: trace JSON            │
│                     │         │  out: grounded prose       │
│ get_cell            │         │                            │
│ find_cells          │         │  (StructuralComparator     │
│ backward_trace      │         │   deferred to v2 — for    │
│ forward_impact      │         │   now the coordinator     │
│ resolve_named_range │         │   handles comparisons     │
│ list_named_ranges   │         │   directly)               │
│ list_findings       │         │                            │
│ what_if             │         │                            │
│ list_sheets         │         │                            │
└─────────┬───────────┘         └────────────┬──────────────┘
          │                                   │
          └────────────┬──────────────────────┘
                       │
                       ▼
       ┌──────────────────────────────────────┐
       │       CITATION AUDITOR                │
       │       Deterministic post-check        │
       │                                       │
       │  Every number, cell ref, named range  │
       │  in the answer → must appear in a     │
       │  tool result from this session.       │
       │                                       │
       │  Fail → retry with violation feedback │
       │  Retry fail → "I don't know" partial  │
       └──────────────────────────────────────┘
```

---

## 4. File plan

### 4.1 Files to CREATE

| File | Purpose | Approx. LOC |
|---|---|---|
| `rosetta/coordinator.py` | Coordinator agent loop: plan → call tools → delegate → synthesize → audit → retry | ~300 |
| `rosetta/auditor.py` | Citation auditor: extract claims from answer text, verify against tool_call_log | ~200 |
| `rosetta/specialists/__init__.py` | Package marker | 1 |
| `rosetta/specialists/formula_explainer.py` | LLM specialist: trace JSON → grounded prose | ~120 |
| `rosetta/conversation.py` | `ConversationState` dataclass + helpers (active_entity extraction, cache keys) | ~150 |
| `docs/plan_v1_5.md` | This doc | — |

### 4.2 Files to MODIFY

| File | Change |
|---|---|
| `rosetta/store.py` | Replace `ChatSession` with `ConversationState` (keep backward-compat alias for existing frontend). Add `AnswerCache`. |
| `rosetta/api.py` | `/chat` endpoint now calls `coordinator.answer(wb, state, message)` instead of `chat.chat(...)`. Add `/chat/{sid}/scenario` (POST + DELETE) for explicit scenario mgmt. |
| `rosetta/chat.py` | Gut it. Becomes a thin compatibility shim that re-exports `coordinator.answer` as `chat`. No hybrid regex logic. |
| `rosetta/tools.py` | Add 2 new tools: `get_workbook_summary`, `scenario_recalc` (built on existing `evaluator.Evaluator`). Update `find_cells` to accept a `tier` param (v1.5: only "exact" and "keyword" tiers implemented; "semantic" tier returns empty + warning, for compatibility with v2). |
| `rosetta/static/app.js` | Display audit status badge on each assistant message. Display active scenario overrides in sidebar with clear button. |
| `rosetta/static/style.css` | Badge styles (green / yellow / red) + scenario chip styles. |
| `rosetta/static/index.html` | Minimal: add a `<div id="scenarios">` slot in the sidebar. |
| `rosetta/qa.py` | Add deprecation docstring. Do not delete. |
| `rosetta/evaluator.py` | Add deprecation docstring. Do not delete. |

### 4.3 Files to KEEP UNCHANGED

- `rosetta/parser.py`
- `rosetta/formula_parser.py`
- `rosetta/graph.py`
- `rosetta/audit.py`
- `rosetta/models.py`
- `fixtures/*`
- `tests/*`
- `data/*`

### 4.4 Files to DELETE

**None.** Deletions are a v2 concern.

---

## 5. Data models (new — to be defined in `rosetta/conversation.py` and `rosetta/store.py`)

### 5.1 `ConversationState`

```python
from dataclasses import dataclass, field
from typing import Any, Optional
import time

@dataclass
class ChatMessage:
    role: str            # "user" | "assistant"
    content: str
    turn_id: int
    timestamp: float = field(default_factory=time.time)

@dataclass
class ToolCall:
    turn_id: int
    tool_name: str
    input: dict
    output: dict
    latency_ms: int
    error: Optional[str] = None

@dataclass
class CachedAnswer:
    question_hash: str
    answer_text: str
    evidence_refs: list[str]
    trace: Optional[dict]
    confidence: float
    cached_at: float = field(default_factory=time.time)

@dataclass
class ConversationState:
    session_id: str
    workbook_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    active_entity: Optional[str] = None           # last cell ref or metric name
    scenario_overrides: dict[str, Any] = field(default_factory=dict)
    answer_cache: dict[str, CachedAnswer] = field(default_factory=dict)
    tool_call_log: list[ToolCall] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def current_turn_id(self) -> int:
        return len([m for m in self.messages if m.role == "user"])
```

### 5.2 Cache key policy

```python
def question_hash(q: str, scenario: dict) -> str:
    import hashlib, json
    normalized = q.lower().strip()
    sig = f"{normalized}::{json.dumps(scenario, sort_keys=True)}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]
```

Rules:
- Cache hit only if `scenario_overrides == {}` OR the scenario signature matches exactly.
- Cache entry TTL: 1 hour (configurable via `ROSETTA_CACHE_TTL_SECS`).
- Cache is per-session. Do not share across sessions.

---

## 6. Tool palette (v1.5 — 11 tools)

All 9 existing tools in `rosetta/tools.py` are kept as-is, with the following additions/modifications:

### 6.1 New: `get_workbook_summary`

```python
{
    "name": "get_workbook_summary",
    "description": "Return a high-level summary of the workbook: sheet names, named range count, circular refs, audit finding counts. Call this once at the start of a new question to orient yourself.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}
```

Returns:
```json
{
  "sheets": [{"name": "...", "rows": 47, "formulas": 128, "hidden": false}, ...],
  "named_range_count": 35,
  "named_ranges_sample": ["FloorPlanRate", "IncentiveRate_Toyota", "..."],
  "has_pivots": false,
  "circular_ref_count": 1,
  "finding_counts": {"stale_assumption": 3, "hardcoded_anomaly": 1}
}
```

### 6.2 New: `scenario_recalc`

```python
{
    "name": "scenario_recalc",
    "description": "Recompute the workbook with one or more input cells overridden. Returns new values for cells that changed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "overrides": {
                "type": "object",
                "description": "Dict of {cell_ref_or_named_range: new_value}"
            },
            "target_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: specific cells to recompute. If omitted, all impacted downstream cells."
            }
        },
        "required": ["overrides"]
    }
}
```

**Implementation:** thin wrapper around existing `rosetta.evaluator.Evaluator`. Composes coordinator-passed overrides with `ConversationState.scenario_overrides` (coordinator merges; tool just uses what it gets). Returns `{"recalculated": dict, "unsupported": list}`.

### 6.3 Modified: `find_cells`

Add `tier` parameter with values `"exact" | "keyword" | "semantic" | "auto"`. In v1.5:
- `exact` — implemented (canonical ref, exact named range name)
- `keyword` — implemented (current behavior — substring match on `semantic_label`, seeded with `CANON_ALIASES` from deprecated `qa.py`)
- `semantic` — **stub that returns `{"matches": [], "note": "semantic tier not available in v1.5"}`**
- `auto` — tries `exact` then `keyword`; if empty, returns empty (does not error)

Frontend/coordinator should treat v1.5's `auto` as "exact → keyword → give up."

---

## 7. Coordinator loop spec (`rosetta/coordinator.py`)

### 7.1 Entry point

```python
def answer(wb: WorkbookModel, state: ConversationState, message: str) -> dict:
    """Produce a grounded answer to the user's question.

    Returns dict with shape:
    {
        "session_id": str,
        "answer": str,
        "trace": Optional[dict],
        "evidence": list[dict],
        "escalated": bool,           # True if specialist was called
        "audit_status": "passed" | "partial" | "unknown",
        "confidence": float,
        "tool_calls_made": int,
    }
    """
```

### 7.2 Turn lifecycle

1. Append user message to `state.messages`.
2. Compute `qh = question_hash(message, state.scenario_overrides)`. Check `state.answer_cache`. Cache hit → verify audit still passes, return cached.
3. Build LLM call:
   - `system = COORDINATOR_SYSTEM_PROMPT` (see 7.3)
   - `messages = _claude_format(state.messages)` + injected context line summarizing `state.active_entity` and `state.scenario_overrides`
   - `tools = TOOLS_V1_5`
4. Loop up to 10 iterations:
   - If `stop_reason == "tool_use"`: for each tool_use block, execute, append output to `state.tool_call_log`, append `tool_result` message. Re-invoke.
   - If `stop_reason == "end_turn"`: proceed to audit.
5. Extract final answer text. Run auditor.
6. If audit passes → cache, return. If audit fails once → re-prompt coordinator with violation list (1 retry only). If second audit fails → return "I don't know" partial answer.
7. Update `state.active_entity` from the final answer (extract first `Sheet!Ref` mentioned).
8. Append assistant message to `state.messages`.

### 7.3 Coordinator system prompt (write to a constant `COORDINATOR_SYSTEM_PROMPT`)

```
You are Rosetta's coordinator. You answer questions about a specific parsed
Excel workbook by calling deterministic tools and, when needed, delegating
to the FormulaExplainer specialist.

CORE RULES — never violate:
1. Every number, percentage, currency value, and cell reference in your
   answer must come from a tool result in this conversation. Never invent
   a value or cell ref.
2. If simple arithmetic is needed (e.g. subtracting two fetched numbers),
   do it yourself but only with values you have just fetched. Cite the
   source refs.
3. Resolve named ranges by NAME AND VALUE (e.g. "FloorPlanRate (5.8%)",
   not "Assumptions!B2").
4. When a question is ambiguous (multiple candidate cells), list the
   candidates and ask — never silently pick.
5. When you cannot ground an answer in tool results, say "I don't know"
   and explain what specifically you couldn't verify.
6. Cite cell refs in canonical form: `Sheet!Ref` (e.g. `P&L Summary!G32`).
7. Do not fabricate cell refs. If a tool returns no results, do not
   continue as if it had.

PLANNING GUIDANCE:
- Unknown workbook? Start with get_workbook_summary.
- "How is X calculated?" → find_cells(X) → backward_trace → delegate to
  FormulaExplainer.
- "What depends on X?" → resolve_named_range or find_cells → forward_impact.
- "What if X changes?" → scenario_recalc with overrides.
- "Stale / issues / hidden / circular" → list_findings.
- Follow-up referring to "it" / "that" / "what about April" → the active
  entity from prior turn is provided in a system-injected context line.

DELEGATION:
- FormulaExplainer(trace_json, original_question) → returns grounded prose.
- Do not call the specialist without passing structured tool data.
- You may call the specialist at most once per turn.

OUTPUT:
- Your final answer is free-form prose but MUST adhere to rules 1–7.
- Cite evidence inline, e.g. "(P&L Summary!G32: $142,300)".
```

### 7.4 "I don't know" path

When the auditor rejects twice, return:

```
I can partially answer this. Here's what I verified:
• <tool-verified facts, bullet list>

What I couldn't verify:
• <each flagged number/ref from the failed audit>

You might rephrase as: <suggestion>.
```

Set `audit_status = "unknown"`, `confidence = 0.3`.

---

## 8. FormulaExplainer spec (`rosetta/specialists/formula_explainer.py`)

### 8.1 Entry point

```python
def explain(trace: dict, original_question: str) -> dict:
    """Convert a backward trace to grounded prose.

    Returns:
        {"prose": str, "warnings": list[str]}
    """
```

### 8.2 System prompt

```
You are Rosetta's FormulaExplainer. You receive a structured backward
trace of an Excel cell and produce a grounded prose explanation in the
style of a senior financial analyst walking a colleague through the
calculation.

STYLE CONTRACT:
1. Cite EVERY number with its cell ref in parentheses: "(P&L Summary!G18: $487,500)"
2. Resolve every named range by name AND value: "FloorPlanRate (5.8%)"
3. Lead with what the cell IS (label + ref + value).
4. Describe the formula in plain business language, not Excel syntax.
5. Walk the dependency tree ONE level deep by default. Go deeper only if
   the explanation requires it (typically for sums of sums).
6. Never round unless the original value is already rounded.
7. Never introduce a number or ref not in the provided trace JSON.
8. If the trace contains warnings (hardcoded, volatile, stale), surface
   them in the explanation.

OUTPUT: Prose only. No headers, no bullets unless the trace has
multiple parallel components (then use a short bulleted list).
```

### 8.3 Input format (contract with coordinator)

```python
{
    "trace": TraceNode (as dict),    # from backward_trace tool
    "original_question": str,         # the user's question verbatim
}
```

### 8.4 Target output example (for Q2)

> Adjusted EBITDA is in cell `P&L Summary!G32` and equals **$142,300**. It's calculated as Total Gross Profit (`P&L Summary!G18`: $487,500) minus Total Operating Expenses (`P&L Summary!G25`: $358,200), plus two addback items from the Assumptions sheet: Owner Compensation Addback (`Assumptions!B15`: $8,000) and One-Time Legal Costs (`Assumptions!B16`: $5,000). The Total Gross Profit itself is a sum of department-level gross from four other sheets: New Vehicle (`New Vehicle!G48`: $148,000), Used Vehicle (`Used Vehicle!G52`: $112,500), F&I (`F&I Detail!G30`: $89,000), and Service & Parts (`Service & Parts!G44`: $138,000).

---

## 9. Citation auditor spec (`rosetta/auditor.py`)

### 9.1 Entry point

```python
def audit(answer_text: str, tool_call_log: list[ToolCall], wb: WorkbookModel) -> AuditResult:
    """Check that every claim in answer_text is grounded in tool outputs."""
```

Returns:
```python
@dataclass
class AuditResult:
    status: str                          # "passed" | "failed"
    violations: list[str]                # e.g. "$487,500 not found in any tool result"
    verified_numbers: list[str]
    verified_refs: list[str]
```

### 9.2 Extraction rules

From the answer text, extract:

1. **Numbers** — regex `[\$]?[-+]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?[%]?`
   - Strip `$`, `,`, and trailing `%`.
   - If `%`, convert: `5.8` → also check `0.058`.
2. **Cell refs** — regex `(?:'[^']+'|[A-Za-z_][\w ]*)!\$?[A-Z]{1,3}\$?[0-9]+`
   - Normalize (strip `$`, quotes around sheet name).
3. **Named ranges** — any identifier in `wb.named_ranges` that appears as a whole word in the answer.

### 9.3 Verification rules

For each extracted token:

- **Number:** must appear (within floating-point tolerance of 0.5%) in any `ToolCall.output` from `tool_call_log`. Also check if it matches any value in `wb.cells[*].value`.
- **Cell ref:** must appear as a key in any tool output that returned cells, OR equal the ref of any cell mentioned by a tool.
- **Named range:** must appear in any tool output, OR be one of the known `wb.named_ranges`.

### 9.4 Allowed without citation

- Structural keywords: "stale", "circular", "hidden", "volatile", "hardcoded" — require `list_findings` to have returned a matching category in this session.
- Zero values (`0`, `$0`, `0%`) — always allowed.
- Dates in any format — allowed if any tool returned a matching date value.
- Counts mentioned that equal `len(some_tool_output_array)`.

### 9.5 Retry prompt template (coordinator uses this on first failure)

```
Your previous answer contained the following unverified claims:
{violations}

Either:
- Remove these claims from your answer, OR
- Call a tool that returns them, then regenerate the answer.

Regenerate now.
```

---

## 10. Frontend updates (minimal)

### 10.1 Audit status badge

In `rosetta/static/app.js` `addMessage("assistant", ...)`, add a badge:

```javascript
const statusEl = document.createElement("span");
statusEl.className = `badge audit-${extras.audit_status}`;  // passed | partial | unknown
statusEl.textContent = {
  "passed": "✓ grounded",
  "partial": "⚠ partial",
  "unknown": "✗ unverified"
}[extras.audit_status];
meta.appendChild(statusEl);
```

CSS:
```css
.badge.audit-passed { background: var(--accent-2); color: #0b0d12; }
.badge.audit-partial { background: var(--warn); color: #0b0d12; }
.badge.audit-unknown { background: var(--error); color: white; }
```

### 10.2 Scenario overrides display

New `<div id="scenarios">` in sidebar. Populated after each `/chat` response:

```javascript
function renderScenarios(overrides) {
  const el = document.getElementById("scenarios");
  el.innerHTML = Object.keys(overrides).length === 0
    ? "<p class='dim'>No active scenarios</p>"
    : Object.entries(overrides).map(([k, v]) =>
        `<div class="scenario-chip">
           <span>${escapeHtml(k)}: ${escapeHtml(fmt(v))}</span>
           <button data-ref="${escapeHtml(k)}" class="clear-scenario">✕</button>
         </div>`
      ).join("");
}
```

Clicking ✕ calls `DELETE /chat/{session_id}/scenario?ref={k}`.

### 10.3 Include `active_entity` and scenarios in `/chat` response

Update backend to include them in the JSON response so the frontend can re-render without a separate fetch.

---

## 11. API surface (v1.5)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/ingest` | multipart file | `{workbook_id, summary}` — unchanged from v1 |
| POST | `/chat` | `{workbook_id, message, session_id?}` | `{session_id, answer, trace?, evidence, escalated, audit_status, confidence, tool_calls_made, active_entity, scenario_overrides}` |
| GET | `/chat/{sid}/history` | — | `{session_id, workbook_id, messages, active_entity, scenario_overrides}` |
| POST | `/chat/{sid}/scenario` | `{overrides: dict}` | `{scenario_overrides}` — replaces; for explicit set |
| DELETE | `/chat/{sid}/scenario` | optional query `?ref=...` | `{scenario_overrides}` — clears one or all |
| GET | `/trace/{wid}/{sheet}/{cell}` | — | unchanged |
| GET | `/audit/{wid}` | — | unchanged |
| GET | `/workbooks` | — | unchanged |

---

## 12. Hour-by-hour execution plan (12h budget with 2h buffer)

| Hour | Work | Exit criterion |
|---|---|---|
| 0–1h | **Scaffolding.** Create `rosetta/coordinator.py`, `rosetta/auditor.py`, `rosetta/specialists/`, `rosetta/conversation.py` as empty modules. Update `rosetta/qa.py` and `rosetta/evaluator.py` with deprecation docstrings. Add new dataclasses to `rosetta/store.py`. | `python3 -c "from rosetta import coordinator, auditor, conversation"` works. |
| 1–3h | **Auditor (§9).** Implement extraction + verification. Unit test with synthetic answers: (a) fully grounded, (b) contains hallucinated number, (c) contains hallucinated ref, (d) qualitative-only ("this is stale"). | 4 unit tests pass in `tests/test_auditor.py`. |
| 3–4h | **ConversationState + cache (§5).** Implement dataclass, `question_hash`, cache get/put. Update `rosetta/store.py` to manage `ConversationState` sessions. | `python3 -c "from rosetta.store import ChatSessionStore; s = ChatSessionStore().create('wb1'); print(s.session_id)"` works and new dataclass is used. |
| 4–5h | **New tools (§6.1, 6.2).** `get_workbook_summary`, `scenario_recalc`. `find_cells` tier parameter. | `curl -X POST /ask` through existing code still works (regression check). Manual test of two new tools via Python REPL. |
| 5–6h | **FormulaExplainer (§8).** Create `rosetta/specialists/formula_explainer.py` with `explain()` function. Test with a hardcoded trace JSON. | Test: pass the Adjusted EBITDA backward trace as JSON, verify prose matches style contract. |
| 6–9h | **Coordinator loop (§7).** Full planning + tool dispatch + audit + retry + "I don't know" path. Delegation to FormulaExplainer. | Ask Q2 ("How is Adjusted EBITDA calculated?") via coordinator directly (no API), get a grounded answer that passes the auditor. |
| 9–10h | **API integration.** Replace `/chat` body. Add `/chat/{sid}/scenario` endpoints. Make `rosetta/chat.py` a thin shim. | All 5 canonical questions answerable via `curl`. |
| 10–11h | **Frontend updates.** Audit badges, scenario chips, display `active_entity`. | Manual test in browser: upload fixture, ask Q2, see green badge; ask what-if, see scenario chip appear. |
| 11–12h | **Verification (§13).** Run all 11 test scenarios. Fix what breaks. | §13.1–§13.3 all passing or explicitly noted as expected failure. |

**If behind schedule at hour 9:** drop frontend updates, ship CLI-only with `curl`. The architecture is the story; UI polish can come in v2.

---

## 13. Verification (must pass before v1.5 is declared done)

Run all of these against both fixtures (`data/dealership_financial_model.xlsx` and `data/energy_portfolio_model.xlsx`).

### 13.1 Canonical 5 questions

| # | Question | Expected tool path | Audit status |
|---|---|---|---|
| Q1 | "What was total gross profit in March?" | find_cells → get_cell | passed |
| Q2 | "How is Adjusted EBITDA calculated?" | find_cells → backward_trace → delegate FormulaExplainer | passed, narrative style matches §8.4 |
| Q3 | "Which cells depend on the Tax Rate assumption?" | resolve_named_range → forward_impact | passed, grouped by sheet |
| Q4 | "Are there any stale assumptions or formulas referencing hidden sheets?" | list_findings (twice) | passed |
| Q5 | "How does the gross profit calculation differ between the New and Used vehicle sheets?" | find_cells x2 → backward_trace x2 → coordinator writes diff directly (no StructuralComparator in v1.5) | passed |

### 13.2 Must-not-hallucinate (3 tests)

| Test | Setup | Expected |
|---|---|---|
| NH-1 | Ask "What's the value in `Ghost!A1`?" (nonexistent sheet) | Answer says "that cell doesn't exist." Audit status: passed (no hallucinated value). |
| NH-2 | Ask "What's the exact March gross profit?" and verify the returned number exactly matches `wb.cells["P&L Summary!D18"].value`, not a rounded approximation | Numeric equality check in test. |
| NH-3 | Ask an ambiguous question: "What's the gross profit?" on a workbook with 4 gross profit cells | Coordinator either lists candidates and asks for clarification, OR picks one with explicit caveat ("Assuming you mean Total Gross Profit for YTD..."). Never silently picks. |

### 13.3 Multi-turn conversation (3 tests)

| Test | Turns | Expected |
|---|---|---|
| MT-1 | T1: "How is EBITDA calculated?"  T2: "What if FloorPlanRate went to 7%?" | T2 uses active_entity (EBITDA) + scenario_recalc. Answer shows new EBITDA with scenario applied. |
| MT-2 | T1: "What if FloorPlanRate = 7%?"  T2: "And what if ReconCostCap = 3000 too?" | T2 scenarios composed (both overrides active). `scenario_overrides` dict has 2 entries. |
| MT-3 | T1: "Use 7% floor plan." T2: "Actually use 6.5%." | T2 replaces (not appends) — `scenario_overrides["FloorPlanRate"] == 0.065`. |

### 13.4 Cache behavior (2 tests)

| Test | Setup | Expected |
|---|---|---|
| C-1 | Ask same question twice in same session, no scenarios | 2nd response < 100ms (cache hit). Response identical. |
| C-2 | Ask same question, then change scenario, then ask again | Cache miss on 3rd call (different scenario signature). |

---

## 14. Deployment (v1.5)

### 14.1 Local run (current)

```bash
# Backend
python3 -m uvicorn rosetta.api:app --host 0.0.0.0 --port 2727

# Frontend
cd rosetta/static && python3 -m http.server 7272
```

### 14.2 Hackathon deploy (pick one)

**Option A — Render.com (recommended, ~2h):**
1. Push `feature/UI_v1` branch to origin.
2. Create Render Web Service from the GitHub repo.
3. Build: `pip install -r requirements.txt`
4. Start: `python3 -m uvicorn rosetta.api:app --host 0.0.0.0 --port $PORT`
5. Env: `ANTHROPIC_API_KEY=...`, `ROSETTA_MODEL=claude-sonnet-4-5`.
6. Serve frontend as static files from the same service (FastAPI already mounts `/static`).

**Option B — Railway (~2h, similar):** identical flow.

**Option C — Fly.io (~3h):** requires `fly.toml` + Dockerfile; more steps but more control.

### 14.3 Environment variables required

```
ANTHROPIC_API_KEY=sk-...              # required
ROSETTA_MODEL=claude-sonnet-4-5       # optional override
ROSETTA_CACHE_TTL_SECS=3600           # optional
AUDIT_STRICT=true                     # optional; default true
```

---

## 15. Open decisions (recorded, not blockers)

From earlier discussion:
- **Qualitative claim strictness** (§9.4) — v1.5 allows qualitative phrases tied to audit finding categories. Revisit in v2 testing whether to tighten or loosen.
- **"I don't know" partial vs. pure refusal** (§7.4) — shipping partial-grounding version. Revisit if users prefer pure refusal.

## 16. Rollback plan

If v1.5 breaks fixture tests that v1 passes:
- `rosetta/qa.py` and `rosetta/evaluator.py` are still in the repo (not deleted)
- `rosetta/chat.py` shim can re-import `qa.answer` as the fallback
- Emergency flag: `ROSETTA_FALLBACK=qa_regex` → `chat.chat()` calls `qa.answer()` instead of `coordinator.answer()`

## 17. What to commit and when

One atomic commit per stage in §12:
- `feat(v1.5): scaffolding and deprecation markers`
- `feat(v1.5): citation auditor with tests`
- `feat(v1.5): ConversationState and answer cache`
- `feat(v1.5): new tools (summary, scenario_recalc) and find_cells tiers`
- `feat(v1.5): FormulaExplainer specialist`
- `feat(v1.5): coordinator loop with audit gate`
- `feat(v1.5): API integration and /scenario endpoints`
- `feat(v1.5): frontend audit badges and scenario chips`
- `feat(v1.5): verification fixes`

All on branch `feature/v1.5-coordinator`. Open PR when stage 8 commits. Merge after §13 passes.

---

**End of v1.5 plan.**
When v1.5 is merged and running, proceed to `docs/plan_v2_upgrades.md` to pick the single v2 upgrade.
