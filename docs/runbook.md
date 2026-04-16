# Rosetta Runbook

## Local development

```bash
# One-time
pip install -r requirements.txt

# Set your Anthropic API key (required for the LLM coordinator)
export ANTHROPIC_API_KEY=sk-ant-...

# Optional overrides
export ROSETTA_MODEL=claude-sonnet-4-5
export ROSETTA_CACHE_TTL_SECS=3600
```

## Run the server

### Option A — single process (backend serves frontend)

```bash
python3 -m uvicorn rosetta.api:app --port 2727 --reload
# open http://localhost:2727
```

### Option B — split frontend / backend

```bash
# Terminal 1: backend
python3 -m uvicorn rosetta.api:app --port 2727

# Terminal 2: frontend
cd rosetta/static && python3 -m http.server 7272
# open http://localhost:7272
```

The frontend's `app.js` auto-detects port 7272 and routes fetches to
`http://localhost:2727`.

---

## Verification checklist (v1.5)

Requires `ANTHROPIC_API_KEY`. Run after starting the server with the key set.

### Canonical questions (§13.1)

Upload `data/dealership_financial_model.xlsx`, then ask each:

- [ ] **Q1** — "What was total gross profit in March?"
  → Expect a specific value cited from `P&L Summary!D18`. Audit badge: `✓ grounded`.

- [ ] **Q2** — "How is Adjusted EBITDA calculated?"
  → Expect a multi-paragraph narrative citing Total Gross Profit, Operating
  Expenses, and addback named ranges. Audit badge: `✓ grounded`.

- [ ] **Q3** — "Which cells depend on the Tax Rate assumption?"
  → Expect a grouped-by-sheet list of cells that depend on `TaxRate`.

- [ ] **Q4** — "Are there any stale assumptions or formulas referencing hidden sheets?"
  → Expect a list of audit findings. Currently the dealership fixture has 9
  stale assumptions and 1 hardcoded anomaly.

- [ ] **Q5** — "How does the gross profit calculation differ between the
  New and Used vehicle sheets?"
  → Expect a diff explanation (Days on Lot, Recon Cost, Floor Plan Interest
  in Used Vehicle but not New Vehicle).

### Must-not-hallucinate tests (§13.2)

- [ ] **NH-1** — "What's the value in `Ghost!A1`?"
  → Expect "that cell doesn't exist" or similar. No invented value.

- [ ] **NH-2** — Ask "What's the exact March gross profit?" and verify the
  returned number exactly equals `wb.cells["P&L Summary!D18"].value`.

- [ ] **NH-3** — "What's the gross profit?" (on a workbook with many gross
  profit cells)
  → Expect candidates listed OR explicit caveat about which one.

### Multi-turn tests (§13.3)

- [ ] **MT-1** —
  - Turn 1: "How is EBITDA calculated?"
  - Turn 2: "What if FloorPlanRate went to 7%?"
  → Turn 2 should use `active_entity` (EBITDA) and show recomputed value.

- [ ] **MT-2** —
  - Turn 1: "What if FloorPlanRate = 7%?"
  - Turn 2: "And what if ReconCostCap = 3000 too?"
  → Scenario chips should show BOTH overrides active. Combined impact.

- [ ] **MT-3** —
  - Turn 1: "Use 7% floor plan."
  - Turn 2: "Actually use 6.5%."
  → Only 6.5% should remain active.

### Cache behavior (§13.4)

- [ ] **C-1** — Ask the same question twice. Second response < 100ms
  (cache hit).
- [ ] **C-2** — Ask, change scenario, ask again. Should be a cache miss.

---

## Unit tests

```bash
# Citation auditor
python3 tests/test_auditor.py
```

Should print `8 passed, 0 failed`.

---

## Diagnostics

```bash
curl http://localhost:2727/diagnostics
```

Returns `version`, whether API key is set, active sessions, etc.

---

## Known limitations (v1.5, fixed in v2)

- **No semantic cell search.** Workbooks with labels not in
  `CANON_ALIASES` may fail on fuzzy queries ("marketing spend" vs.
  "Ad Spend"). Fixed by v2 Option A (Qdrant).
- **Custom evaluator coverage.** `scenario_recalc` silently returns
  cached values for formulas the custom evaluator can't handle. Fixed
  by v2 Option B (`formulas` pip).
- **No pivot table introspection.** Fixed by later v2 work.

See `docs/plan_v2_upgrades.md` for the v2 upgrade plan.
