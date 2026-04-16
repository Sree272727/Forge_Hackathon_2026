# Rosetta — Excel Intelligence Agent

**Teach the machine to read the spreadsheet the way its author thinks about it — not as a grid of numbers, but as a web of logic.**

Rosetta ingests complex Excel workbooks, extracts their full computational structure (formulas, dependencies, named ranges, cross-sheet references, pivot hints, structural regions, hidden elements), and answers natural-language questions about them — with **grounded, traceable explanations**, never guesses.

Rosetta is exposed as a FastAPI service so platforms like Lens can invoke it: send a workbook, ask a question, get an answer with a formula trace and confidence.

## Current version: v2A

**Agentic coordinator with citation auditor + semantic cell retrieval.**

- **Coordinator agent** (Claude Sonnet 4.5) plans each question and
  calls deterministic tools over the parsed workbook
- **FormulaExplainer specialist** generates grounded narrative for
  formula-explanation questions
- **Citation auditor** verifies every number, cell ref, and named range
  in the answer against tool results — returns "I don't know" before
  ever hallucinating
- **Three-tier cell lookup**: exact → keyword → semantic (Qdrant +
  local sentence-transformers, falls back automatically)
- **Multi-turn conversation memory** with active_entity tracking and
  scenario override stacking for what-if questions

See [docs/architecture_v2.md](docs/architecture_v2.md) for the full design,
[docs/plan_v1_5.md](docs/plan_v1_5.md) for the v1.5 foundation, and
[docs/plan_v2_upgrades.md](docs/plan_v2_upgrades.md) for the v2 upgrade
plan. Deployment in [docs/deployment.md](docs/deployment.md).

---

## Why this is different

Most Excel-reading tools stop at cell values. Rosetta parses the **computational graph**:

- **Formulas**, tokenized and classified (aggregation, lookup, conditional, cross-sheet, ...)
- **Dependencies**, forward and backward, across sheets
- **Named ranges**, resolved to cell targets
- **Structural regions** per sheet (header / data / subtotal / summary)
- **Hidden rows, hidden sheets, merged cells**
- **Circular references** (flagged as intentional iterative calcs)
- **Volatile functions** (`NOW`, `TODAY`, `OFFSET`, `INDIRECT`)
- **Hardcoded anomalies** (rows where a formula is expected)
- **Stale assumptions** (assumption rows with dates > 12 months old)

Everything an answer claims is derived from deterministic parsing. The LLM (optional, via `ANTHROPIC_API_KEY`) only **rewords** grounded answers — it never invents a formula or reference.

---

## Quickstart

```bash
# 1. Install
pip install -r requirements.txt

# 2. Generate the demo workbooks
python3 fixtures/generate_dealership.py data/dealership_financial_model.xlsx
python3 fixtures/generate_energy.py data/energy_portfolio_model.xlsx

# 3. Run the demo script (no server needed)
python3 demo.py all

# 4. Start the API
uvicorn rosetta.api:app --reload --port 8000
```

Then:

```bash
# Ingest
curl -s -X POST -F "file=@data/dealership_financial_model.xlsx" http://localhost:8000/ingest | jq

# Ask
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"workbook_id":"<id>","question":"How is Adjusted EBITDA calculated?"}' | jq

# Backward/forward trace
curl -s "http://localhost:8000/trace/<id>/P&L%20Summary/B32" | jq

# Audit findings
curl -s http://localhost:8000/audit/<id> | jq

# What-if
curl -s -X POST http://localhost:8000/what-if \
  -H "Content-Type: application/json" \
  -d '{"workbook_id":"<id>","assumption":"FloorPlanRate","new_value":0.07}' | jq
```

---

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/ingest` | Upload `.xlsx`, returns `workbook_id` + structural summary |
| POST | `/ask` | Grounded Q&A — returns answer + evidence + trace + warnings + confidence |
| GET | `/trace/{workbook_id}/{sheet}/{cell}` | Full backward + forward dependency trace for a cell |
| GET | `/audit/{workbook_id}` | Diagnostic findings (stale, hidden, hardcoded, circular, volatile, broken) |
| POST | `/what-if` | Override an assumption value; returns all impacted cells and changed outputs |
| GET | `/workbooks` | List ingested workbooks |

---

## Question types Rosetta handles

| Intent | Example | Engine |
|--------|---------|--------|
| `value` | "What was total gross profit in March?" | Cell + month-column resolution |
| `formula` | "How is Adjusted EBITDA calculated?" | Backward trace + plain-language explanation |
| `dependency` | "What would change if I updated the floor plan rate?" | Forward impact, grouped by sheet, key outputs flagged |
| `diagnostic` | "Are there any stale assumptions?" | Audit engine |
| `comparative` | "Compare the structure of these sheets." | Formula-type histogram per sheet |
| `cross_join` | "What's the combined F&I revenue and front gross for Deal #1047?" | Cross-sheet row join |
| `what_if` | "What would happen if the degradation rate was 1%?" | Safe evaluator runs recomputation |

---

## Architecture

```
┌────────────────────┐   ┌─────────────────────┐   ┌────────────────────┐
│ .xlsx (via POST)   │──▶│ Parser (openpyxl)   │──▶│ WorkbookModel      │
└────────────────────┘   │  + formula_parser   │   │ (cells, deps, NRs, │
                         │  + precompute pass  │   │  regions, findings)│
                         └─────────────────────┘   └─────────┬──────────┘
                                                             │
                          ┌──────────────────────────────────┼──────────────────────────────┐
                          ▼                                  ▼                              ▼
                   ┌─────────────┐                   ┌──────────────┐               ┌──────────────┐
                   │ Graph:      │                   │ Audit:       │               │ Evaluator:   │
                   │  backward/  │                   │  stale, hid, │               │  safe partial│
                   │  forward    │                   │  circular,   │               │  formula run │
                   │  traces     │                   │  hardcoded   │               │  for what-if │
                   └─────────────┘                   └──────────────┘               └──────────────┘
                          │                                  │                              │
                          └──────────────────┬───────────────┴──────────────────────────────┘
                                             ▼
                                   ┌─────────────────────┐
                                   │ Q&A engine          │
                                   │  classify → retrieve│
                                   │  → ground → answer  │
                                   └──────────────────────┘
                                             │
                                             ▼
                                        FastAPI
```

### Modules

| Module | Role |
|--------|------|
| `rosetta/parser.py` | openpyxl-based structural extraction + precompute pass |
| `rosetta/formula_parser.py` | Deterministic formula reference extraction (token-based) |
| `rosetta/graph.py` | Backward/forward dependency traversal |
| `rosetta/audit.py` | Diagnostics — stale, hidden, circular, hardcoded, volatile, broken |
| `rosetta/evaluator.py` | Partial safe formula executor (SUM/IF/VLOOKUP/SUMIFS/...) for what-if |
| `rosetta/qa.py` | Intent classification + grounded answer assembly |
| `rosetta/api.py` | FastAPI endpoints |
| `rosetta/models.py` | Pydantic data models |
| `rosetta/store.py` | In-memory workbook store |
| `fixtures/generate_dealership.py` | Deterministic dealership test workbook generator |
| `fixtures/generate_energy.py` | Deterministic energy portfolio generator |

---

## Test workbooks

### Dealership (6 sheets, 330+ formulas)

- Cross-sheet P&L Summary pulling from New Vehicle, Used Vehicle, F&I, Service & Parts
- Named ranges: `FloorPlanRate`, `IncentiveRate<OEM>`, `TaxRate`, `OwnerCompensationAddback`, ...
- Intentional circular reference: Service Absorption ↔ Overhead
- 3+ stale assumptions with dates > 12 months old
- 2 hidden rows with deprecated assumption entries
- Hardcoded anomaly on Used Vehicle row 23 (should be a formula)
- Key metrics: Total Gross Profit, Adjusted EBITDA, Service Absorption Rate

### Energy Portfolio (7 sheets, 340+ formulas)

- 10 sites with PPA / Merchant / Hybrid conditional revenue logic
- Multiplicative system loss: `1 - PRODUCT(1-loss_i)` — not additive (common mistake)
- VLOOKUP-based price curves
- Per-site assumption overrides for Alpha (degradation) and Gamma (escalation cap)
- `INDIRECT` formula for dynamic sheet reference on Price Curves sheet

---

## What Rosetta supports (explicit)

**Fully supported:**
- Formula token extraction, cross-sheet references, named ranges, quoted sheet names
- Backward/forward dependency traces across sheets
- Audit checks for stale, hidden, circular, hardcoded, volatile, broken refs
- What-if recomputation for `+ - * / ^ &` arithmetic, `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `PRODUCT`, `IF`, `IFERROR`, `AND`, `OR`, `NOT`, `ROUND`, `ABS`, `SUMIF[S]`, `COUNTIF[S]`, `AVERAGEIF[S]`, `SUMPRODUCT`, `VLOOKUP`, `XLOOKUP`, `INDEX`, `MATCH`, `DATE`, `YEAR`, `MONTH`, `DAY`, `TODAY`, `NOW`

**Partial:**
- Pivot tables — metadata extracted, but calculated-field recomputation is not supported
- Array / dynamic array formulas — extracted and explained but not executed
- `OFFSET`, `INDIRECT` — flagged as volatile, not executed in what-if

When a formula cannot be recomputed, it is reported in `WhatIfResponse.unsupported_formulas` and the cached value is retained — answers never silently return zero.

---

## Grounding guarantees

- Every cell reference, formula, and value in an answer is pulled from the parsed model.
- If an LLM polish step is enabled (`ANTHROPIC_API_KEY` set), the prompt forbids inventing facts — the LLM may only reword.
- Warnings are surfaced explicitly (hardcoded inputs, volatile functions, circular references, stale assumptions).

---

## Run the demo

```bash
python3 demo.py dealership   # dealership workbook only
python3 demo.py energy        # energy portfolio only
python3 demo.py all           # both
```

The demo prints the ingestion summary plus Q&A walk-throughs matching the hackathon rubric.
