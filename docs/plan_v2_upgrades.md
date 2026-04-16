# Rosetta v2 Upgrades — Post-v1.5 Plan

**Status:** PLANNED for execution after v1.5 is shipped and merged
**Target:** Pick ONE upgrade path (A or B) and execute in 6–8 hours
**Owner context:** This document is self-contained. A fresh Claude session should be able to execute from this file plus `docs/plan_v1_5.md` and `docs/architecture_v2.md`.

---

## 1. Context

v1.5 shipped a coordinator + citation auditor + FormulaExplainer specialist on top of the existing parser and evaluator. It answers the 5 canonical questions without hallucinating.

v1.5 is **missing two production-grade capabilities** that the full v2 architecture (`docs/architecture_v2.md`) calls for. We commit to adding ONE of them before the hackathon demo (per "Plan C" — v1.5 plus one targeted upgrade).

Pick Option A or Option B based on §2 below. Do not attempt both in 6–8 hours. Commit to the choice before starting.

---

## 2. Option picker

Decide BEFORE starting work. Criteria:

### Option A — Qdrant + Semantic Cell Search
**Choose this if:**
- Demo will include (or may include) a workbook the system has never seen before
- Demo story emphasizes "works on any customer's workbook"
- You want maximum "wow" moment during demo (semantic retrieval is visible)
- Keyword + alias matching feels fragile on v1.5 end-to-end tests

**Expected build time:** 6–8h
**Biggest risk:** sentence-transformers model download (800MB) on first run; Qdrant schema iteration

### Option B — `formulas` pip + evaluator_v2
**Choose this if:**
- Demo will stress-test what-if scenarios and scenario composition
- You've hit formula-coverage bugs during v1.5 testing (evaluator.py silently wrong on some formulas)
- Production story prioritizes "correct numbers over fuzzy matching"
- You're less concerned about demoing on unseen workbooks

**Expected build time:** 4–6h
**Biggest risk:** `formulas` pip has its own cell-key format, quirks with named ranges, may miss coverage on specific formulas in fixtures

### Default recommendation: **Option A**.

Rationale: the "no hallucinations" story is already told by v1.5's citation auditor. Option A adds "works on any workbook." Option B adds "correct what-if" — but the existing evaluator is already well-exercised against the fixtures, so the delta is smaller in visible demo value. Option B is the stronger production upgrade; Option A is the stronger demo upgrade.

---

## 3. Option A — Qdrant + Semantic Cell Search

### 3.1 What gets added

A third tier in `find_cells` (see v1.5 plan §6.3). When exact + keyword matching both fail, a semantic embedding similarity search runs against a Qdrant collection containing **rich cell context strings** for every labeled cell in the workbook.

This unlocks the system working on workbooks with unfamiliar labels ("Ad Spend by Channel" instead of "Advertising", "Gross Margin" instead of "Gross Profit", etc.).

### 3.2 Target architecture delta

```
INGEST PIPELINE (v1.5)                         INGEST PIPELINE (v2A)
─────────────────────                         ─────────────────────
parse_workbook                                parse_workbook
audit_workbook                                audit_workbook
store.put(wb)                                 ┌─ NEW ────────────────────┐
                                              │ build_cell_contexts(wb)   │
                                              │ → list[CellContext]       │
                                              │                           │
                                              │ embed_contexts(ctxs)      │
                                              │ → numpy.ndarray[N, 384]   │
                                              │                           │
                                              │ qdrant.upsert(            │
                                              │   collection_name=        │
                                              │   f"rosetta_{wid}",      │
                                              │   vectors, payloads       │
                                              │ )                         │
                                              └───────────────────────────┘
                                              store.put(wb)

find_cells(query, tier="auto")                find_cells(query, tier="auto")
├─ exact                                      ├─ exact
├─ keyword                                    ├─ keyword
└─ semantic → RETURNS []                      └─ semantic → Qdrant query
```

### 3.3 Files to create

| File | Purpose | Approx. LOC |
|---|---|---|
| `rosetta/embeddings.py` | `Embedder` class wrapping sentence-transformers; `QdrantIndex` wrapper | ~180 |
| `rosetta/cell_context.py` | `CellContext` dataclass + `build_cell_contexts(wb)` function | ~150 |
| `docker-compose.yml` | Qdrant service definition | ~15 |
| `tests/test_embeddings.py` | Sanity tests: embed a known cell, query should rank it top | ~60 |
| `docs/plan_v2_upgrades.md` | This doc | — |

### 3.4 Files to modify

| File | Change |
|---|---|
| `rosetta/parser.py` | Call `build_cell_contexts(wb)` after parsing, attach to `WorkbookModel`. |
| `rosetta/models.py` | Add `cell_contexts: list[CellContext]` field to `WorkbookModel`. |
| `rosetta/api.py` | `/ingest` runs embedding pipeline after parse. Requires Qdrant reachable. |
| `rosetta/tools.py` | Implement `semantic` tier in `find_cells`. Query Qdrant, return top-K. |
| `requirements.txt` | Add `qdrant-client>=1.9`, `sentence-transformers>=2.7`, `numpy` if not present. |
| `rosetta/store.py` | On workbook delete (if implemented), delete Qdrant collection too. |
| `.gitignore` | Add `qdrant_storage/` |

### 3.5 Data model: `CellContext`

```python
@dataclass
class CellContext:
    ref: str                        # "P&L Summary!G32"
    sheet: str
    coord: str                      # "G32"
    semantic_label: Optional[str]   # "Adjusted EBITDA — Mar"
    row_header: Optional[str]       # "Adjusted EBITDA"
    col_header: Optional[str]       # "Mar 2026"
    formula_type: Optional[str]     # "cross_sheet_calculation"
    is_summary_cell: bool           # lives in a subtotal region
    is_major_output: bool           # deep dep tree AND business-sounding label
    context_string: str             # what we embed

    def build_context_string(self) -> str:
        parts = [
            self.sheet,
            self.row_header,
            self.col_header,
            self.formula_type,
            "summary" if self.is_summary_cell else None,
            "major_output" if self.is_major_output else None,
        ]
        return " / ".join(p for p in parts if p)
```

Example context strings that will be embedded:
- `"P&L Summary / Adjusted EBITDA / Mar 2026 / cross_sheet_calculation / summary / major_output"`
- `"Used Vehicle / Floor Plan Interest / Row 15 / arithmetic"`
- `"Assumptions / FloorPlanRate / Value / hardcoded"`

### 3.6 Embedder (`rosetta/embeddings.py`)

```python
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, CPU-friendly
EMBEDDING_DIM = 384

class Embedder:
    _model: SentenceTransformer | None = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            cls._model = SentenceTransformer(EMBEDDING_MODEL)
        return cls._model

    @classmethod
    def embed(cls, texts: list[str]) -> list[list[float]]:
        model = cls.get_model()
        return model.encode(texts, normalize_embeddings=True).tolist()


class QdrantIndex:
    def __init__(self, url: str = "http://localhost:6333"):
        self.client = QdrantClient(url=url)

    def ensure_collection(self, workbook_id: str):
        name = f"rosetta_{workbook_id}"
        try:
            self.client.get_collection(name)
        except Exception:
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )

    def upsert_cells(self, workbook_id: str, contexts: list[CellContext]):
        name = f"rosetta_{workbook_id}"
        self.ensure_collection(workbook_id)
        texts = [c.context_string for c in contexts]
        vectors = Embedder.embed(texts)
        points = [
            PointStruct(
                id=i,
                vector=vec,
                payload={"ref": c.ref, "sheet": c.sheet, "coord": c.coord,
                         "semantic_label": c.semantic_label,
                         "context_string": c.context_string,
                         "is_major_output": c.is_major_output}
            )
            for i, (vec, c) in enumerate(zip(vectors, contexts))
        ]
        self.client.upsert(collection_name=name, points=points)

    def search(self, workbook_id: str, query: str, limit: int = 10) -> list[dict]:
        name = f"rosetta_{workbook_id}"
        qvec = Embedder.embed([query])[0]
        results = self.client.search(collection_name=name, query_vector=qvec, limit=limit)
        return [{"ref": r.payload["ref"],
                 "label": r.payload.get("semantic_label"),
                 "score": r.score,
                 "context": r.payload.get("context_string")}
                for r in results]
```

### 3.7 `find_cells` semantic tier update

In `rosetta/tools.py`:

```python
def _find_cells_semantic(wb: WorkbookModel, query: str, limit: int) -> dict:
    from .embeddings import QdrantIndex
    import os
    try:
        idx = QdrantIndex(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
        results = idx.search(wb.workbook_id, query, limit=limit)
        matches = [{"ref": r["ref"], "label": r["label"],
                    "score": r["score"], "tier_used": "semantic"}
                   for r in results if r["score"] > 0.4]  # threshold
        return {"matches": matches, "count": len(matches)}
    except Exception as e:
        return {"matches": [], "error": str(e), "note": "semantic tier unavailable"}
```

Update the `auto` tier logic to call `_find_cells_semantic` if both exact and keyword return empty AND the query has no exact cell ref pattern.

### 3.8 Infrastructure setup

**`docker-compose.yml`:**

```yaml
version: "3.9"
services:
  qdrant:
    image: qdrant/qdrant:v1.11.3
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./qdrant_storage:/qdrant/storage
    restart: unless-stopped
```

Run before the server:
```bash
docker compose up -d qdrant
# Wait for healthcheck
until curl -s http://localhost:6333/healthz > /dev/null; do sleep 0.5; done
```

### 3.9 Hour-by-hour plan (Option A, 8h budget)

| Hour | Work | Exit criterion |
|---|---|---|
| 0–1h | **Infrastructure.** Docker compose file, `docker compose up -d qdrant`, install new pip packages, trigger cold model download (one-time ~800MB). | `docker ps` shows Qdrant running. `python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"` completes. |
| 1–3h | **`cell_context.py` + `embeddings.py`.** Build contexts from `WorkbookModel`. Test: ingest dealership fixture, confirm ~200 cells get contexts. Upsert to Qdrant. Query "EBITDA" and see Adjusted EBITDA cell in top 3. | Test in `tests/test_embeddings.py` passes. |
| 3–5h | **`find_cells` semantic tier.** Implement the semantic branch, update `auto` tier logic. Handle Qdrant-unavailable gracefully (fall back to keyword, log warning). | Coordinator, when asked a question with a fuzzy label, correctly finds the cell via semantic tier. |
| 5–6h | **Ingest pipeline integration.** `POST /ingest` now builds contexts + embeds + upserts. Test: ingest both fixtures, query Qdrant directly to verify. | `curl /ingest` succeeds; manual Qdrant query returns expected points. |
| 6–7h | **End-to-end verification.** Run all v1.5 tests. Then: add a "fuzzy label" test — ask a question using a synonym not in `CANON_ALIASES`. Verify semantic tier catches it. | All v1.5 tests still pass. New fuzzy-label test passes. |
| 7–8h | **Deployment.** Add Qdrant to deployment plan. For Render.com: Qdrant as a separate service or use Qdrant Cloud free tier (recommended). Update env vars. Deploy. | Deployed instance ingests a fixture and answers Q2 correctly in the browser. |

### 3.10 Verification (Option A specific)

In addition to v1.5's §13, add:

| Test | Setup | Expected |
|---|---|---|
| SEM-1 | Ask "What's our marketing spend?" on a workbook where the label is "Ad Spend by Channel" | `find_cells` returns the Ad Spend cell via semantic tier. |
| SEM-2 | Ask "Show me the gross margin" on a workbook that has "Gross Profit %" | Returns Gross Profit % cell. |
| SEM-3 | Ingest a totally unseen workbook (not in fixtures). Ask "what's our biggest revenue line?" | Coordinator lists candidates via semantic search. |

### 3.11 Deployment changes for Option A

**Qdrant hosting options:**
- **Qdrant Cloud** (recommended for hackathon): free tier 1GB cluster. Set `QDRANT_URL=https://your-cluster.qdrant.tech` and `QDRANT_API_KEY`.
- **Self-hosted on Render/Railway**: deploy Qdrant as a separate service; same region as API for latency.
- **Embedded mode**: `QdrantClient(path="./qdrant_storage")` — no server, embedded in the Python process. Viable for demo; loses data on restart.

**Env vars added:**
```
QDRANT_URL=http://localhost:6333      # or cloud URL
QDRANT_API_KEY=                        # for cloud only
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

### 3.12 Risks & mitigations (Option A)

| Risk | Impact | Mitigation |
|---|---|---|
| Model download takes 5+ min first time | Delays startup | Pre-download in Dockerfile or on first deploy; cache model in volume |
| Qdrant cold in prod is 30s+ startup | Slow first ingest | Warm it with a dummy collection on service start |
| Semantic search returns noise (low threshold) | Wrong cell picked | Threshold of 0.4 is conservative; tune on fixtures |
| Large workbooks (10k+ cells) slow to embed | Ingest time | Batch embed (256 texts at a time); optionally embed only cells with labels |
| Demo machine can't run Docker | Setup failure | Use embedded Qdrant path mode as fallback; no Docker needed |

---

## 4. Option B — `formulas` pip + `evaluator_v2`

### 4.1 What gets added

Replace the custom 879-line evaluator (`rosetta/evaluator.py`) with a thin wrapper around the `formulas` pip package. This gives us:
- Full coverage of ~400+ Excel functions (vs. ~30 currently supported)
- Proper handling of SUMIFS with complex criteria, XLOOKUP, dynamic arrays
- Iterative circular reference resolution
- Ability to handle workbooks with formulas we haven't manually supported

### 4.2 Target architecture delta

```
CURRENT (v1.5)                             NEW (v2B)
──────────────                             ─────────
Evaluator (879 LOC custom)                 WorkbookEvaluator (thin wrapper)
├─ _tokenize (custom)                      └─ formulas.ExcelModel
├─ _eval_formula (custom)
├─ ~30 function impls
└─ silently returns None on unsupported    Returns (value, was_computed) per cell
```

`scenario_recalc` tool is rewired to call the new evaluator. No coordinator changes.

### 4.3 Files to create

| File | Purpose | Approx. LOC |
|---|---|---|
| `rosetta/evaluator_v2.py` | `WorkbookEvaluator` wrapping `formulas.ExcelModel` | ~120 |
| `tests/test_evaluator_v2.py` | Parity tests against v1 evaluator on fixtures | ~80 |

### 4.4 Files to modify

| File | Change |
|---|---|
| `rosetta/tools.py` | `scenario_recalc` tool now uses `evaluator_v2` instead of `Evaluator`. |
| `rosetta/api.py` | `/what-if` endpoint now uses `evaluator_v2`. Path passed instead of `WorkbookModel`. |
| `rosetta/parser.py` | After parsing, also instantiate `WorkbookEvaluator(xlsx_path)` and attach to `WorkbookModel` (or store in `WorkbookStore` alongside). |
| `requirements.txt` | Add `formulas>=1.2`. |
| `rosetta/evaluator.py` | Keep (deprecation docstring already added in v1.5). Mark even more clearly as "do not use; retained for reference." |

### 4.5 `WorkbookEvaluator` (`rosetta/evaluator_v2.py`)

```python
import formulas
from pathlib import Path
from typing import Any

class WorkbookEvaluator:
    """Wrapper around formulas.ExcelModel for recomputation with overrides."""

    def __init__(self, xlsx_path: str | Path):
        self.xlsx_path = str(xlsx_path)
        self.xl_model = formulas.ExcelModel().loads(self.xlsx_path).finish()
        self.xl_model.calculate()
        # Capture book name for key normalization
        self._book_name = Path(self.xlsx_path).name

    def _normalize_ref(self, ref: str) -> str:
        """Rosetta uses 'Sheet!A1'; formulas uses '[Book]Sheet!A1'."""
        if ref.startswith("["):
            return ref.upper()
        return f"[{self._book_name}]{ref}".upper()

    def _denormalize_ref(self, ref: str) -> str:
        """Inverse of _normalize_ref."""
        if ref.startswith("["):
            return ref.split("]", 1)[1]
        return ref

    def value_of(self, ref: str, overrides: dict[str, Any] | None = None) -> tuple[Any, bool]:
        """Return (value, was_computed). was_computed=False means fell back to cached."""
        norm_ref = self._normalize_ref(ref)
        norm_overrides = {
            self._normalize_ref(k): v for k, v in (overrides or {}).items()
        }
        try:
            solution = self.xl_model.calculate(inputs=norm_overrides,
                                                 outputs=[norm_ref])
            val = solution.get(norm_ref)
            return (val, True)
        except Exception:
            return (None, False)

    def recalculate_all(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Recompute all outputs with overrides. Returns dict[ref, value]."""
        norm_overrides = {self._normalize_ref(k): v for k, v in overrides.items()}
        solution = self.xl_model.calculate(inputs=norm_overrides)
        return {self._denormalize_ref(k): v for k, v in solution.items()}
```

### 4.6 `scenario_recalc` tool update

In `rosetta/tools.py`:

```python
def _scenario_recalc(wb: WorkbookModel, overrides: dict, target_refs: list[str] | None) -> dict:
    from .evaluator_v2 import WorkbookEvaluator
    # WorkbookEvaluator lives on store for the workbook
    evaluator = store.get_evaluator(wb.workbook_id)
    if not evaluator:
        return {"error": "evaluator not available for this workbook"}

    recalculated = {}
    unsupported = []
    for ref in (target_refs or [c.ref for c in wb.cells.values() if c.formula]):
        val, ok = evaluator.value_of(ref, overrides=overrides)
        if ok:
            recalculated[ref] = val
        else:
            unsupported.append(ref)
    return {"recalculated": recalculated, "unsupported": unsupported}
```

### 4.7 Store update

```python
class WorkbookStore:
    def __init__(self):
        self._wbs = {}
        self._evaluators = {}   # NEW
        self._lock = threading.Lock()

    def put(self, wb: WorkbookModel, xlsx_path: str):
        with self._lock:
            self._wbs[wb.workbook_id] = wb
            try:
                from .evaluator_v2 import WorkbookEvaluator
                self._evaluators[wb.workbook_id] = WorkbookEvaluator(xlsx_path)
            except Exception as e:
                log.warning("Could not initialize WorkbookEvaluator: %s", e)

    def get_evaluator(self, wid: str):
        return self._evaluators.get(wid)
```

### 4.8 Hour-by-hour plan (Option B, 6h budget)

| Hour | Work | Exit criterion |
|---|---|---|
| 0–1h | **Infrastructure.** Install `formulas` pip. Sanity check: load a fixture with `formulas.ExcelModel`, calculate a known cell, verify result matches the v1 evaluator. | Python REPL: `WorkbookEvaluator(path).value_of("P&L Summary!G18")` returns the known gross profit. |
| 1–2h | **`evaluator_v2.py`.** Write the wrapper. Handle ref normalization edge cases (quoted sheets, named ranges, `$` signs). | `tests/test_evaluator_v2.py` parity tests pass. |
| 2–3h | **Parity testing.** Run both evaluators over every formula cell in both fixtures. Log any discrepancies. Investigate each. Most will be: (a) v1 was wrong and v2 is correct, or (b) v1 was correct and v2 hits unsupported. | Parity report written to `tests/evaluator_parity_report.md`. |
| 3–4h | **Tool + API integration.** Rewire `scenario_recalc` and `/what-if` to use v2. Attach `WorkbookEvaluator` to `WorkbookStore` on ingest. | `curl /what-if` works and returns v2-computed values. |
| 4–5h | **End-to-end verification.** Run all v1.5 tests. What-if tests should now have broader coverage (e.g. INDIRECT-based cells work). | Multi-turn what-if tests (MT-1, MT-2) pass. Coordinator tells user explicitly when a formula couldn't be recomputed (previously silent). |
| 5–6h | **Deployment.** No new services. Deploy updated API. | Deployed instance handles a what-if question correctly in the browser. |

### 4.9 Verification (Option B specific)

In addition to v1.5's §13, add:

| Test | Setup | Expected |
|---|---|---|
| EV-1 | Pick 10 formula cells from each fixture. Compute with v1 and v2. | Values match within floating-point tolerance. |
| EV-2 | Ask "what if FloorPlanRate = 7%" — compare old v1 result to new v2 result | Same direction of change, similar magnitude. If different, document why. |
| EV-3 | Ask what-if on a cell the v1 evaluator flagged as unsupported. E.g. a cell using INDIRECT. | v2 either computes it correctly OR reports "unsupported" explicitly (never silently wrong). |

### 4.10 Risks & mitigations (Option B)

| Risk | Impact | Mitigation |
|---|---|---|
| `formulas` pip returns different values than v1 on edge cases | Demo inconsistency | Parity report in hour 2–3 catches this. Document deliberate diffs. |
| `formulas` has its own bugs on SUMIFS or array formulas | Silent wrong answer | LibreOffice headless as secondary validator on ingest (post-demo enhancement) |
| Cell ref normalization breaks with quoted sheet names | Errors on any formula referencing 'P&L Summary' | Write explicit test cases for the two fixtures' sheet-name patterns |
| Install time on Render/Railway balloons (formulas pulls in scipy etc.) | Slow deploys | Pin version; pre-build wheel if needed |

---

## 5. Regardless of option — always do these

### 5.1 Final documentation

After shipping the chosen upgrade, update these files:

- `README.md` — describe the agentic architecture, citation auditor, whichever upgrade was shipped
- `docs/architecture_v2.md` — mark the shipped pieces as "DONE (v1.5)" or "DONE (v2A)" / "DONE (v2B)"
- `docs/demo_script.md` (NEW) — exact questions to ask in the demo, expected responses, fallback plan if something breaks

### 5.2 Demo rehearsal

Spend 30 min before the actual demo:
1. Clean browser state (clear localStorage).
2. Cold-start the server.
3. Ingest a fixture fresh.
4. Run every question from `docs/demo_script.md` in order.
5. Note any latency spikes or flaky responses.
6. If any break: either skip in the demo, or fix now if <10 min to fix.

### 5.3 Observability snapshot

Add to `api.py` a simple `/diagnostics` endpoint:

```python
@app.get("/diagnostics")
def diagnostics():
    return {
        "version": "v1.5" or "v2A" or "v2B",
        "workbooks_loaded": len(store.list()),
        "active_sessions": len(chat_store._sessions),
        "embedding_model": os.environ.get("EMBEDDING_MODEL"),          # if A
        "evaluator": "formulas>=1.2" if using_v2 else "custom",        # if B
        "uptime_seconds": int(time.time() - _start_time),
    }
```

Useful during demo if something misbehaves and you need to debug live.

### 5.4 Git hygiene

Each v2 upgrade goes on its own branch:
- Option A: `feature/v2a-qdrant-semantic`
- Option B: `feature/v2b-formulas-evaluator`

Merge into `main` only after verification passes. Keep `feature/UI_v1` (v1.5) as a checkpoint branch.

---

## 6. What remains deferred after v1.5 + one v2 upgrade

These are explicitly **not** in v1.5 OR the v2 upgrade. They remain in `docs/architecture_v2.md` as the full roadmap.

- The **other** v2 option (whichever wasn't picked). Schedule post-demo.
- StructuralComparator specialist agent
- `compute` tool (deterministic arithmetic)
- `list_pivots` tool
- `compare_sheet_structure` tool
- `version_diff` tool and workbook versioning
- Async ingest / job queue
- Multi-tenancy / auth / encryption at rest
- LibreOffice headless fallback
- Voyage AI embeddings (swap from bge-small)
- Production observability (Datadog, structured logging)
- Fine-tuned intent classifier (almost certainly not needed)

When picking the next thing to build post-demo, re-read `docs/architecture_v2.md` §15 — it lists these with follow-up slots.

---

## 7. Sign-off checklist (before starting v2 upgrade)

- [ ] v1.5 is merged to main and passing all §13 verification tests
- [ ] Decision made between Option A and Option B (record it here):
  - Option chosen: **_____**
  - Reason: **_____**
  - Date: **_____**
- [ ] Demo deadline known: **_____**
- [ ] Hours available for this upgrade: **_____** (must be ≥ 6h for A, ≥ 4h for B)
- [ ] Rollback plan: if the v2 upgrade breaks v1.5 behavior, revert the merge and ship v1.5 alone.

---

**End of v2 upgrades plan.**

The full, unreduced vision for Rosetta lives in `docs/architecture_v2.md`. This file is the pragmatic next step. The v1.5 execution plan lives in `docs/plan_v1_5.md`.
