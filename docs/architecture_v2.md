# Rosetta v2 — Agentic Excel Intelligence

**Status:** Proposed plan, pending approval
**Author:** Design discussion 2026-04-16
**Supersedes:** The regex-router + single-tool-loop v1 in `rosetta/qa.py` and `rosetta/chat.py`

---

## 0. Why this document exists

The current Rosetta is a good v1. It answers the 8 pre-built hackathon questions by routing through regex intents and polishing answers with an LLM. It has a hand-rolled 879-line evaluator, a keyword-based cell finder, and an in-memory session store.

We now want to turn it into an **agentic, production-grade Excel Q&A system** that is honest about what it doesn't know, never hallucinates numbers, and reasons across any workbook — not just the two demo fixtures. This document is the blueprint for that rebuild.

---

## 1. Product principles (design non-negotiables)

1. **No hallucinated numbers.** Every number or cell reference in any answer must be traceable to a tool call result from this conversation. If it can't be cited, it doesn't appear. The product says "I don't know" before it guesses.
2. **Deterministic where possible, LLM only where reasoning genuinely differs.** Parsing, tracing, recalculation, and audit are code. Explanation, planning, and comparison are LLM. The split is strict.
3. **Grounded explanations, flexible prose.** Answers are free-form natural language, but constrained by a style contract: cite every number, resolve every named range, never round unless asked.
4. **Production shape from day one.** Versioned workbooks, observable tool calls, cache discipline, "I don't know" as a first-class output. We'll defer implementing a few (async ingest, multi-tenancy, encryption at rest) but the architecture won't need rewrites to accept them.
5. **Robust on unseen workbooks.** The system must work on a customer's workbook we've never tuned for. That means semantic cell resolution, structural inference, and no hardcoded aliases.

---

## 2. Architecture overview

```
                                 ┌──────────────────────────────────────┐
                                 │              INGEST PIPELINE          │
  .xlsx ─────────────────────────▶  parse_workbook (openpyxl x2)         │
                                 │  → dependency_graph (networkx)        │
                                 │  → audit (stale/circular/volatile/…)  │
                                 │  → cell_context_index (rich strings)  │
                                 │  → embeddings (local s-t) → Qdrant    │
                                 │  → workbook_version_hash              │
                                 └──────────────────┬───────────────────┘
                                                    │
                                                    ▼
                             ┌──────────────────────────────────────────┐
                             │         CONVERSATION STATE (per chat)     │
                             │  • workbook_id + version_hash             │
                             │  • message history                         │
                             │  • active_entity (last cell/metric/nr)    │
                             │  • scenario_overrides: dict[ref, value]   │
                             │  • answer_cache: dict[question_hash, QA]  │
                             └──────────────────┬───────────────────────┘
                                                │
                                                ▼
                         ┌──────────────────────────────────────────┐
                         │             COORDINATOR AGENT             │
                         │       Claude Sonnet 4.5, temp=0           │
                         │       tools=DETERMINISTIC_TOOLS            │
                         │       system prompt = PRINCIPLES_PROMPT    │
                         │                                            │
                         │  Plans → calls tools → maybe delegates →   │
                         │  synthesizes final answer → audited        │
                         └──┬──────────────────────────────────┬─────┘
           tool calls       │                                   │ delegate
                            ▼                                   ▼
          ┌────────────────────────────────┐       ┌────────────────────────────────┐
          │   DETERMINISTIC TOOLS           │       │  SPECIALIST AGENTS              │
          │   (pure Python, no LLM)         │       │  (narrow-scope Claude calls)    │
          ├────────────────────────────────┤       ├────────────────────────────────┤
          │ get_cell                        │       │ FormulaExplainer                │
          │ find_cells (3-tier)             │       │  in:  trace JSON + context      │
          │   └ tier1: exact ref / nr name  │       │  out: grounded prose            │
          │   └ tier2: keyword on labels    │       │                                 │
          │   └ tier3: semantic via Qdrant  │       │ StructuralComparator            │
          │ backward_trace                  │       │  in:  two traces + schemas      │
          │ forward_impact                  │       │  out: diff explanation          │
          │ resolve_named_range             │       │                                 │
          │ list_named_ranges               │       │ (Future slots:                  │
          │ list_findings                   │       │  AnomalyNarrator,               │
          │ list_pivots                     │       │  ScenarioPlanner)               │
          │ scenario_recalc (formulas pkg)  │       │                                 │
          │ compute (arithmetic only)       │       │                                 │
          │ compare_sheet_structure         │       │                                 │
          │ get_workbook_summary            │       │                                 │
          └────────────────────────────────┘       └────────────────────────────────┘
                                                │
                                                ▼
                                 ┌──────────────────────────────┐
                                 │   CITATION AUDITOR            │
                                 │   Deterministic post-check.   │
                                 │                               │
                                 │   Every number / ref / named  │
                                 │   range in the answer must    │
                                 │   appear in a tool result     │
                                 │   from this turn or prior.    │
                                 │                               │
                                 │   Fail → retry once with      │
                                 │   violation feedback.         │
                                 │   Retry fail → "I don't know" │
                                 │   partial answer.             │
                                 └──────────────────────────────┘
```

---

## 3. Component inventory

### 3.1 Keep (existing code to build on)

| File | Why keep |
|---|---|
| `rosetta/parser.py` | Solid openpyxl-based parser. Extend, don't rewrite. |
| `rosetta/formula_parser.py` | Custom tokenizer for ref extraction. Good enough. |
| `rosetta/graph.py` | `backward_trace`, `forward_impacted` — clean. |
| `rosetta/audit.py` | Stale/hardcoded/volatile/circular detection. |
| `rosetta/models.py` | `WorkbookModel`, `TraceNode`, `AuditFinding` — solid. |
| `rosetta/tools.py` | 9 tools already defined; will extend with new ones. |
| `rosetta/static/*` | Frontend stays. Minor UX tweaks only. |

### 3.2 Modify

| File | Change |
|---|---|
| `rosetta/api.py` | New endpoints: `POST /ingest` becomes async-capable, `POST /chat` now routes through coordinator, `GET /workbook/{id}/versions`. |
| `rosetta/store.py` | Add `ConversationState` (richer than current `ChatSession`), `AnswerCache`, workbook versioning. |
| `rosetta/chat.py` | Rewrite — becomes the coordinator loop with audit gate. The hybrid regex-first-then-LLM logic is removed. |
| `rosetta/tools.py` | Add: `find_cells_semantic`, `scenario_recalc` (new evaluator), `compute`, `compare_sheet_structure`, `list_pivots`, `get_workbook_summary`, `version_diff`. Update `find_cells` to be 3-tier. |
| `requirements.txt` | Add: `formulas`, `qdrant-client`, `sentence-transformers`. |

### 3.3 Create

| File | Purpose |
|---|---|
| `rosetta/coordinator.py` | The coordinator agent — planning loop, tool dispatch, specialist delegation, citation audit, "I don't know" path. |
| `rosetta/specialists/formula_explainer.py` | LLM specialist that takes a trace JSON and writes grounded prose. |
| `rosetta/specialists/structural_comparator.py` | LLM specialist that diffs two traces or sheet structures. |
| `rosetta/auditor.py` | Citation auditor: regex-extract numbers/refs/names from answer, verify each against tool result log. |
| `rosetta/embeddings.py` | Local sentence-transformers embedder + Qdrant client wrapper. Handles ingest + query. |
| `rosetta/cell_context.py` | Rich context builder: `"{sheet} / {row_header} / {col_header} / {formula_type} / {is_summary}"` per cell, for semantic indexing. |
| `rosetta/evaluator_v2.py` | Thin wrapper around `formulas.ExcelModel` exposing `recalculate(overrides)` and `value_of(ref)`. |
| `docker-compose.yml` | Qdrant service. |
| `docs/architecture_v2.md` | This document. |

### 3.4 Delete (technical debt)

| File | Why delete |
|---|---|
| `rosetta/evaluator.py` | 879-line custom evaluator. `formulas` pip replaces it. Deleting removes a whole class of silent-wrong-answer bugs. |
| `rosetta/qa.py` | Regex intent classifier + per-intent handlers. The coordinator subsumes this. Keep `CANON_ALIASES` for seeding tier-2 matching. |

---

## 4. Data models (new)

### 4.1 `ConversationState`

```python
@dataclass
class ConversationState:
    session_id: str
    workbook_id: str
    workbook_version_hash: str                 # invalidates state on re-ingest
    messages: list[ChatMessage]                 # full history
    active_entity: Optional[CellRef | str]     # "the thing we were just discussing"
    scenario_overrides: dict[CellRef, Any]     # what-if stack
    answer_cache: dict[str, CachedAnswer]      # question_hash -> answer
    tool_call_log: list[ToolCall]              # for citation audit across turns
    created_at: datetime
    updated_at: datetime
```

### 4.2 `ToolCall` (observability)

```python
@dataclass
class ToolCall:
    turn_id: int
    tool_name: str
    input: dict
    output: dict
    latency_ms: int
    error: Optional[str]
```

### 4.3 `CachedAnswer`

```python
@dataclass
class CachedAnswer:
    question_hash: str                         # hash(normalized_question + workbook_version)
    answer_text: str
    evidence: list[CellRef]
    confidence: float
    cached_at: datetime
    scenario_signature: Optional[str]          # if non-empty scenario, we don't cache
```

### 4.4 `CellContext` (for semantic index)

```python
@dataclass
class CellContext:
    ref: CellRef                               # "P&L Summary!G32"
    sheet: str
    coord: str
    semantic_label: Optional[str]              # "Adjusted EBITDA — Mar"
    row_header: Optional[str]
    col_header: Optional[str]
    formula_type: Optional[str]
    is_summary_cell: bool                      # lives in a "subtotal" region
    is_major_output: bool                      # deep dep tree + business label
    context_string: str                         # the embed-me string
```

---

## 5. Tool palette (exact contract)

All tools are **pure Python**, log every call to `ConversationState.tool_call_log`, and return JSON-serializable dicts.

| Tool | Input | Output | Notes |
|---|---|---|---|
| `get_workbook_summary` | `{}` | `{sheets, named_ranges, findings_count, has_pivots, has_circular, workbook_version}` | Coordinator calls this first on any new question to orient. |
| `get_cell` | `{ref: "Sheet!A1"}` | `{ref, value, formula, semantic_label, depends_on, depended_by, named_ranges_used, is_hardcoded, is_volatile}` | Existing. |
| `find_cells` | `{query: str, limit: int=10, tier: "auto"|"exact"|"keyword"|"semantic"}` | `{matches: [{ref, label, value, score, tier_used}]}` | New 3-tier. `auto` tries each tier in order, returns as soon as ≥N confident matches. |
| `backward_trace` | `{ref, max_depth: int=6}` | `{trace: TraceNode}` | Existing. |
| `forward_impact` | `{ref, max_results: int=100}` | `{total_impacted, by_sheet: {sheet: [{ref, label, value, depth}]}}` | Existing. |
| `resolve_named_range` | `{name: str}` | `{name, scope, resolves_to, current_value, is_dynamic, used_by_cells: [...]}` | Add `used_by_cells`. |
| `list_named_ranges` | `{}` | `{named_ranges: [...]}` | Existing. |
| `list_findings` | `{category?: str, severity?: str}` | `{findings: [...]}` | Existing. |
| `list_pivots` | `{}` | `{pivots: [{sheet, name, source_range, row_fields, col_fields, value_fields, calculated_fields}]}` | **New.** Extract from openpyxl `pivots`. |
| `scenario_recalc` | `{overrides: dict[ref, value], target_refs?: list[ref]}` | `{recalculated: dict[ref, new_value], unsupported: list[ref]}` | **New.** Built on `formulas` pip. Composes with `ConversationState.scenario_overrides`. |
| `compute` | `{expression: str, bindings: dict[str, number]}` | `{value, expression_resolved}` | **New.** The LLM's arithmetic crutch. Only accepts `+ - * / ( )` and bound names. Coordinator cannot do math in-head. |
| `compare_sheet_structure` | `{sheet_a, sheet_b}` | `{column_diff, region_diff, formula_pattern_diff}` | **New.** For Q5-style questions. |
| `version_diff` | `{workbook_id, from_hash, to_hash}` | `{changed_cells: [...], added, removed}` | **New.** For "what changed since last month" questions. Deferred to v1.1 if time pressured. |

---

## 6. Three-tier cell resolution

The single most important tool is `find_cells`. It determines whether the system works on unseen workbooks.

**Tier 1 — Exact (`< 1ms`):**
- Query looks like a canonical ref (`P&L Summary!G32`) → direct lookup.
- Query matches a named range name exactly (case-insensitive) → resolve.
- If 1 match, return and stop.

**Tier 2 — Keyword (`< 10ms`):**
- Case-insensitive substring match against `semantic_label` of every cell.
- Seeded with `CANON_ALIASES` (imported from old `qa.py`) so "EBITDA" matches "Adjusted EBITDA".
- Rank by formula presence, depth of dep tree, label specificity.
- Return top-K if confidence above threshold.

**Tier 3 — Semantic (`< 100ms`):**
- Embed query with sentence-transformers (local, in-process).
- Qdrant cosine similarity over `CellContext.context_string` embeddings.
- Return top-K with similarity scores.

**Coordinator behavior:**
- Default: call `find_cells(query, tier="auto")`.
- Tier 1 + Tier 2 + Tier 3 tried in order; stop when confidence clears threshold.
- If all three tiers fail: coordinator reports "no matching cells found" and either asks a clarifying question or takes the "I don't know" path.

---

## 7. Coordinator loop

### 7.1 System prompt (outline — full text TBD at implementation)

```
You are Rosetta's coordinator agent. You answer questions about a specific
parsed Excel workbook by calling deterministic tools and, when needed,
delegating to specialist agents.

CORE RULES — violating these is an error:
1. Every number, percentage, currency value, and cell reference in your
   answer must come from a tool result. Never invent.
2. Never do arithmetic in your head. Call `compute` with bindings.
3. Resolve named ranges by NAME AND VALUE (e.g. "FloorPlanRate (5.8%)").
4. When a question is ambiguous (e.g. "gross profit" could mean multiple
   cells), ask which one or list candidates — never silently pick.
5. When you don't have the data to answer confidently, say so explicitly.
   "I don't know" is better than "I guess."
6. Cite cell refs in canonical form: `Sheet!Ref`.

PLANNING GUIDANCE:
- Start with `get_workbook_summary` if you haven't this session.
- "How is X calculated?" → `find_cells` → `backward_trace` →
  delegate to FormulaExplainer.
- "What depends on X?" → `resolve_named_range` or `find_cells` →
  `forward_impact`.
- "What if X changes?" → `scenario_recalc` with overrides.
- "How do A and B differ?" → two `backward_trace` calls → delegate to
  StructuralComparator.
- Diagnostic questions (stale/hidden/anomaly) → `list_findings`.
- Conversational follow-ups referring to "it" or "that" → consult
  active_entity in the context.

DELEGATION:
You may delegate to:
- FormulaExplainer(trace_json, context) — returns grounded prose for a
  formula trace.
- StructuralComparator(trace_a, trace_b) — returns a diff explanation.
You MUST NOT call a specialist without passing structured tool data.
```

### 7.2 Turn lifecycle

```
1. Receive user message.
2. Load ConversationState; update active_entity if message contains a reference.
3. Check answer_cache (question_hash + workbook_version + empty scenario).
   Hit → return cached answer, verified against current tool_call_log.
4. Invoke coordinator LLM with:
   - system prompt
   - message history
   - current active_entity and scenario_overrides (as system-injected context)
   - tools = DETERMINISTIC_TOOLS
5. Loop while stop_reason == "tool_use":
   a. Execute each tool call; append to tool_call_log.
   b. If tool is "delegate_to_specialist", invoke the specialist and pass
      its output back as a tool_result.
   c. Re-invoke coordinator with tool results appended.
6. On stop_reason == "end_turn":
   a. Extract final text answer.
   b. Run CitationAuditor(answer, tool_call_log).
   c. If audit passes: return answer + optional trace + evidence refs.
   d. If audit fails: re-prompt coordinator ONCE with violation list.
   e. If second audit fails: return "I don't know" partial answer with
      what we did verify + suggest reformulation.
7. Update ConversationState; possibly cache answer.
```

### 7.3 "I don't know" path — concrete shape

When the auditor rejects twice, the user gets:

> I can partially answer this. Here's what I verified:
> - [list of tool-verified facts]
>
> What I couldn't verify:
> - [specific data points that failed audit]
>
> You might rephrase as: [suggestion based on which tier of find_cells failed].

This is a good response, not a failure mode. Production UX that reveals the boundaries of its knowledge is trustworthy.

---

## 8. Specialist agents

### 8.1 FormulaExplainer

**Role:** Take a backward trace (potentially multi-level) and write a grounded prose explanation in the style of:
> "Adjusted EBITDA is in `P&L Summary!G32`. It's calculated as Total Gross Profit (`G18`: $487,500) minus Total Operating Expenses (`G25`: $358,200), plus two addback items from the Assumptions sheet: Owner Compensation Addback (`Assumptions!B15`: $8,000) and One-Time Legal Costs (`Assumptions!B16`: $5,000). The Total Gross Profit itself is a sum of department-level gross from four other sheets: New Vehicle ($148,000), Used Vehicle ($112,500), F&I ($89,000), and Service & Parts ($138,000)."

**Input:** `{trace: TraceNode, conversation_context: str, style: "narrative"|"bullet"|"technical"}`

**Behavior:**
- Single LLM call. The trace is recursive but compact.
- System prompt specifies the style contract (cite every number with its ref, resolve every named range to name+value, never round, explain what each dependency means in business terms).
- Produces prose only. No tool calls.

**Why single LLM call, not recursion into sub-agents:**
Nested formula decomposition (`IF(SUMIFS(...), VLOOKUP(...), 0)`) is handled by the parser producing a complete AST + trace. The LLM sees the whole tree and writes one coherent explanation. Sub-agents would fragment reasoning and cost 3-5x more tokens.

### 8.2 StructuralComparator

**Role:** Diff two traces or two sheet structures and explain what differs.

**Input:** `{left: TraceNode | SheetSchema, right: TraceNode | SheetSchema, question: str}`

**Behavior:**
- Single LLM call.
- Receives structured diff from `compare_sheet_structure` or two traces.
- Writes prose diff explanation.

---

## 9. Citation auditor

Pure Python. No LLM.

**Extraction:** From the final answer text, extract:
- All numeric patterns (`$487,500`, `5.8%`, `8000`, `2,101.76`, etc.)
- All cell refs matching `[sheet]!Ref` pattern
- All named range names (titlecase or ALL_CAPS identifiers that match a known named range from the workbook)

**Verification:** For each extracted token:
- Is there a matching value in any `ToolCall.output` in this session's `tool_call_log`?
- Numbers must match within floating-point tolerance; cell refs must match exactly; named ranges must match case-insensitively against `workbook.named_ranges`.

**Failure handling:**
- First failure → append structured violation to coordinator's next turn:
  > "Your answer contained the following unverified numbers/references: X, Y, Z. Either cite them via a tool call or remove them."
- Second failure → return "I don't know" partial answer.

**Exceptions:**
- Qualitative phrases ("stale", "circular", "hidden") must trace to an audit finding category, not a numeric value.
- Zero (`$0`, `0%`) is always verifiable.
- Dates match against any date value in tool outputs.

---

## 10. Ingest pipeline

### 10.1 Steps

```
1. Accept .xlsx upload.
2. Compute workbook_version_hash = sha256(file_bytes).
3. Check store: if same hash exists, skip re-parse, return existing workbook_id.
4. parse_workbook(path) → WorkbookModel.
5. audit_workbook(wb) → findings.
6. build_cell_contexts(wb) → list[CellContext].
7. embed_and_upsert(cell_contexts) → Qdrant collection {workbook_id}.
8. store.put(wb).
9. Return {workbook_id, version_hash, summary}.
```

### 10.2 Cell context construction (critical for retrieval quality)

For each cell with a value or formula:

```
context_string = " / ".join(filter(None, [
    sheet_name,                                 # "P&L Summary"
    nearest_row_header(cell),                   # "Adjusted EBITDA"
    nearest_column_header(cell),                # "Mar 2026"
    formula_type,                                # "cross_sheet_calculation"
    "summary" if in_subtotal_region else "",
    "major_output" if is_major_output else "",
]))
# → "P&L Summary / Adjusted EBITDA / Mar 2026 / cross_sheet_calculation / major_output"
```

Embed with `sentence-transformers/bge-small-en-v1.5` (384-dim, CPU-friendly).

### 10.3 Qdrant collection schema

- Collection name: `rosetta_cells_{workbook_id}`
- Vector: 384-dim, cosine
- Payload: `{ref, sheet, coord, semantic_label, context_string, has_formula, is_major_output}`
- On workbook re-ingest with new hash: create new collection, delete old after TTL.

---

## 11. Evaluator (replacement)

Delete `rosetta/evaluator.py`. Create `rosetta/evaluator_v2.py`:

```python
import formulas

class WorkbookEvaluator:
    def __init__(self, xlsx_path: str):
        self.xl_model = formulas.ExcelModel().loads(xlsx_path).finish()
        self.xl_model.calculate()

    def recalculate(self, overrides: dict[str, Any]) -> dict[str, Any]:
        # overrides: {"[Book]Sheet!A1": 0.065}
        solution = self.xl_model.calculate(overrides)
        return solution

    def value_of(self, ref: str, overrides: dict | None = None) -> Any:
        solution = self.xl_model.calculate(overrides or {})
        return solution.get(ref)
```

Wrap in `scenario_recalc` tool. On unsupported formula, return the ref in the `unsupported` list — coordinator surfaces this to the user.

**Reserve:** LibreOffice headless validation at ingest time to cross-check `formulas` against ground truth. Not exposed to users; runs as a CI check on fixtures.

---

## 12. API surface

### 12.1 New / changed endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest` | Upload workbook. Returns `{workbook_id, version_hash, summary}`. Idempotent on same hash. |
| `POST` | `/chat` | `{workbook_id, session_id?, message}` → `{session_id, answer, evidence, trace?, confidence, audit_status, escalated}`. |
| `GET` | `/chat/{session_id}/history` | Full conversation state (messages + scenario_overrides + active_entity). |
| `POST` | `/chat/{session_id}/scenario` | `{overrides: dict[ref, value]}` — explicit scenario set (alternative to asking via chat). |
| `DELETE` | `/chat/{session_id}/scenario` | Clear scenarios. |
| `GET` | `/trace/{workbook_id}/{sheet}/{cell}` | Deterministic trace (no LLM). |
| `GET` | `/audit/{workbook_id}` | All findings. |
| `GET` | `/workbook/{workbook_id}` | Summary + version info. |

### 12.2 Frontend changes

Minimal. Existing frontend already handles chat, upload, and displays traces. Updates:
- Display `audit_status` badge on each assistant message (green = passed, yellow = partial, red = "I don't know").
- Show active scenario overrides in the sidebar ("Floor Plan Rate: 7% (scenario)") with a "clear" button.
- Show which tools the coordinator called for each answer (collapsible "How I got this" section).

---

## 13. Infrastructure

### 13.1 New dependencies

```
formulas>=1.2           # Excel recalculation engine
qdrant-client>=1.9      # Vector DB client
sentence-transformers>=2.7  # Local embeddings (CPU-friendly)
```

### 13.2 Services

```yaml
# docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333"]
    volumes: ["./qdrant_storage:/qdrant/storage"]
```

Run `docker compose up -d qdrant` before the FastAPI server.

### 13.3 Environment variables

```
ANTHROPIC_API_KEY=...
ROSETTA_MODEL=claude-sonnet-4-5
QDRANT_URL=http://localhost:6333
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
AUDIT_STRICT=true                    # hard-fail on unverified numbers
ROSETTA_CACHE_TTL_SECS=3600
```

---

## 14. Verification plan

We verify end-to-end against the 5 canonical questions plus 3 failure modes.

### 14.1 Canonical questions

| # | Question | Expected path | Pass criterion |
|---|---|---|---|
| Q1 | "What was total gross profit in March?" | `find_cells("total gross profit")` → `get_cell` for Mar column | Returns the exact value from `P&L Summary!D18` with correct citation. |
| Q2 | "How is Adjusted EBITDA calculated?" | `find_cells` → `backward_trace(depth=3)` → delegate FormulaExplainer | Narrative matches the target style, every number cited, named ranges resolved by name+value. Audit passes. |
| Q3 | "Which cells depend on the Tax Rate assumption?" | `resolve_named_range("TaxRate")` → `forward_impact` | Grouped-by-sheet list of impacted cells with labels and values. |
| Q4 | "Are there any stale assumptions? Which formulas reference hidden sheets?" | `list_findings("stale_assumption")` + `list_findings("hidden_dependency")` | Full list with locations, dates, and messages. |
| Q5 | "How does the gross profit calculation differ between New and Used vehicle sheets?" | `find_cells` x2 → `backward_trace` x2 → `compare_sheet_structure` → delegate StructuralComparator | Prose diff explaining column presence (Days on Lot, Recon), formula differences (floor plan interest), and structural differences. |

### 14.2 Must-not-hallucinate tests

| Test | Setup | Expected behavior |
|---|---|---|
| Missing cell | Ask "What's the value in `ZZZ!A1`?" | "That cell doesn't exist in this workbook." Not a guess. |
| Unsupported formula in what-if | Scenario involving an INDIRECT-based cell the evaluator can't recompute | Answer includes "I couldn't recompute X because its formula uses INDIRECT" — no silent wrong number. |
| Ambiguous metric | Ask "What's the gross profit?" on a workbook with 4 gross-profit cells | Coordinator lists the 4 candidates and asks which. |

### 14.3 Multi-turn tests

| Test | Setup | Expected behavior |
|---|---|---|
| Follow-up with "it" | Q: "How is EBITDA calculated?" → Q: "What if FloorPlanRate went to 7%?" | Second question uses active_entity + scenario_recalc to show new EBITDA. |
| Scenario stacking | Q: "What if FloorPlanRate = 7%?" → Q: "And if ReconCostCap = 3000?" | Second question composes scenarios; shows combined impact. |
| Scenario reset | "Actually use 6.5% not 7%" | Previous 7% scenario replaced, not appended. |

### 14.4 Production hygiene tests

| Test | Expected |
|---|---|
| Same question asked twice in one session | Second is cache hit, < 50ms. |
| Re-upload same workbook | Version hash unchanged, no re-parse, no re-embed. |
| Re-upload modified workbook | New version hash, new Qdrant collection, prior conversation state invalidated for that workbook. |
| Workbook with 10k+ cells | Ingest < 30s, per-question p95 < 5s. |

---

## 15. What v1 explicitly does NOT ship

Documented so we don't pretend otherwise. Each has a clear follow-up slot.

| Deferred | Why deferred | Where it slots in later |
|---|---|---|
| Async ingest + job queue | 1 uvicorn worker is fine for single-user demo. | Swap FastAPI background task → Celery/RQ when multi-user. |
| Multi-tenancy / auth | Hackathon + internal use first. | Add JWT middleware + per-tenant Qdrant namespacing. |
| Encryption at rest | Local-only for now. | AWS KMS + encrypted volume for Qdrant + workbook store. |
| Version diff UX | Non-trivial UI. | Ship `version_diff` tool; UI later. |
| LibreOffice fallback for unsupported formulas | `formulas` pkg covers 90%; the 10% is rare in financial models. | Shell-out wrapper if specific customer blocks on it. |
| Voyage AI embeddings | Local bge-small is sufficient for cell labels. | 1-line swap in `embeddings.py`. |
| OCR / chart reading | Out of scope — Excel structural intelligence only. | Separate service if ever needed. |
| Fine-tuned intent model | LLM coordinator handles planning. | Only if latency or cost forces it. |

---

## 16. Open decisions (non-blocking for build start)

1. **FormulaExplainer depth policy.** For deeply nested formulas (7+ levels), does it recurse in a single prompt with truncation warnings, or split into sub-prompts? Ship with single-prompt + truncation; revisit if outputs degrade.
2. **Answer cache invalidation semantics.** Is a workbook version change the only invalidator, or do scenario overrides also invalidate prior non-scenario answers in the same session? Ship with: scenarios don't invalidate non-scenario cache hits (they're orthogonal); but scenario-based answers are never cached.
3. **Rate limiting.** Per-session LLM call cap? No limit in v1, add if cost spikes.
4. **Logging / observability backend.** Stdout + local file for v1. Swap to structured JSON → Datadog/Honeycomb for production.

---

## 17. Implementation order (when approved)

Roughly 9 stages. Each is independently testable.

1. **`evaluator_v2.py`** on top of `formulas` pip. Replace `evaluator.py`. Verify all existing `/what-if` tests pass.
2. **`cell_context.py` + `embeddings.py` + Qdrant docker.** Ingest pipeline emits embeddings. Verify with a manual query.
3. **Extended `tools.py`**: `find_cells_semantic` tier added, `compute`, `list_pivots`, `compare_sheet_structure`, `scenario_recalc` reworked.
4. **`auditor.py`** — citation auditor with unit tests on synthetic answers.
5. **`ConversationState`** in `store.py` — replace existing `ChatSession`. Add `scenario_overrides`, `tool_call_log`, `answer_cache`.
6. **`specialists/formula_explainer.py`** and **`specialists/structural_comparator.py`** — isolated, testable with fixture inputs.
7. **`coordinator.py`** — the full loop. Replace `chat.py`'s existing logic.
8. **API updates** in `api.py`. Frontend badge + scenario display.
9. **End-to-end verification** against §14.

---

## 18. Sign-off needed

Before writing any code for this, we want explicit approval on:

- [ ] Section 2 architecture diagram and component split
- [ ] Section 3 keep/modify/create/delete list (especially **deleting `evaluator.py` and `qa.py`**)
- [ ] Section 5 tool palette — any missing tools or wrong signatures
- [ ] Section 7 coordinator system prompt direction
- [ ] Section 9 citation auditor rules (what's verifiable, what's allowed qualitative)
- [ ] Section 14 verification plan — are these the right tests?
- [ ] Section 15 deferred list — anything promoted to v1?

Once signed off, implementation proceeds in the order in §17.
