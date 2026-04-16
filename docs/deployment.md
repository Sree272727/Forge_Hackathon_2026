# Rosetta Deployment Guide (v2A)

## Local development — 3 modes

### Mode 1: Single process (simplest)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m uvicorn rosetta.api:app --port 2727 --reload
# open http://localhost:2727
```

Qdrant runs in **embedded mode** on `./qdrant_storage/`. No Docker needed.

### Mode 2: Qdrant Docker + single-process API

```bash
docker compose up -d qdrant
export QDRANT_URL=http://localhost:6333
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m uvicorn rosetta.api:app --port 2727 --reload
```

Use this if you want persistence across API restarts, or plan to scale to
multiple API workers.

### Mode 3: Split frontend / backend

```bash
# Terminal 1
python3 -m uvicorn rosetta.api:app --port 2727

# Terminal 2
cd rosetta/static && python3 -m http.server 7272
# open http://localhost:7272
```

---

## Production deployment — Render.com (recommended, ~30 min)

### 1. Qdrant — pick one

**A. Qdrant Cloud (recommended, free tier 1GB)**
1. Sign up at https://cloud.qdrant.io/
2. Create a free cluster (choose smallest region-matching instance)
3. Copy the cluster URL and API key

**B. Qdrant as a Render private service** (more work; do this only if Cloud is unavailable)
1. New → Private Service → Docker image `qdrant/qdrant:v1.11.3`
2. Add disk mount at `/qdrant/storage`
3. Port `6333`

### 2. API — Render Web Service

1. New → Web Service → Connect GitHub repo `Sree272727/Forge_Hackathon_2026`
2. Branch: `feature/v2a-qdrant-semantic` (or `main` after PR merge)
3. **Build command:** `pip install -r requirements.txt`
4. **Start command:** `python3 -m uvicorn rosetta.api:app --host 0.0.0.0 --port $PORT`
5. **Environment variables:**
   ```
   ANTHROPIC_API_KEY=sk-ant-...            # required
   ROSETTA_MODEL=claude-sonnet-4-5         # optional
   QDRANT_URL=https://xxxxxx.cloud.qdrant.tech  # from Qdrant Cloud
   QDRANT_API_KEY=xxxxxx                    # from Qdrant Cloud
   EMBEDDING_MODEL=BAAI/bge-small-en-v1.5  # optional
   ROSETTA_CACHE_TTL_SECS=3600             # optional
   ```
6. **Instance type:** Standard (at least 2GB RAM; sentence-transformers needs it)
7. Deploy. First boot takes ~3–5 min (model download).

### 3. Verify

```bash
curl https://your-service.onrender.com/diagnostics
# Expect: version=v2A, anthropic_api_key_set=true, semantic_search_available=true

curl -X POST -F "file=@data/dealership_financial_model.xlsx" \
     https://your-service.onrender.com/ingest
# Expect: semantic_index.indexed_cells > 0

curl -X POST -H "content-type: application/json" \
     -d '{"workbook_id":"WB_ID","message":"How is Adjusted EBITDA calculated?"}' \
     https://your-service.onrender.com/chat
# Expect: audit_status=passed, tool_calls_made > 0
```

### 4. Frontend

The FastAPI app serves `rosetta/static/index.html` at `/` — no separate
frontend deployment needed. `https://your-service.onrender.com/` is the UI.

---

## Railway.app (alternative, similar flow)

```bash
railway init
railway up
# Set env vars in dashboard: ANTHROPIC_API_KEY, QDRANT_URL, QDRANT_API_KEY
```

---

## Fly.io (most control, most setup)

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Pre-download embedding model to speed cold start
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"
COPY . .
CMD ["python3", "-m", "uvicorn", "rosetta.api:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
fly launch
fly secrets set ANTHROPIC_API_KEY=sk-ant-... QDRANT_URL=... QDRANT_API_KEY=...
fly deploy
```

---

## Observability & troubleshooting

### Healthcheck endpoints

- `GET /` — frontend HTML (200 if API is up)
- `GET /api` — JSON service descriptor (easier for load balancer health checks)
- `GET /diagnostics` — full stack status: API key present, workbooks loaded,
  Qdrant mode, embedding model

### Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `/chat` returns "ANTHROPIC_API_KEY not set" | env var missing | Set `ANTHROPIC_API_KEY` |
| `/ingest` returns `semantic_index.error: ImportError` | pip install incomplete | Rerun `pip install -r requirements.txt` |
| First ingest takes 60+ seconds | Model cold download (~100MB) | Pre-bake into Docker image; subsequent ingests < 10s |
| `semantic_index.indexed_cells: 0` but no error | Workbook has zero labeled cells | Check that parser extracted `semantic_label` for cells |
| Qdrant "path locked" error | Multiple processes sharing embedded mode | Use `QDRANT_URL` with Docker/Cloud Qdrant |
| Answer audit status always "partial" | Citation auditor being too strict | Check `rosetta/auditor.py` tolerances; consider relaxing `TOLERANCE_RELATIVE` |

### Log levels

```bash
uvicorn rosetta.api:app --log-level debug
```

Every tool call is logged with `rosetta.coordinator` / `rosetta.tools`
loggers. Useful to trace what the coordinator is doing per question.

---

## Scaling notes (deferred work, not in v2A)

- **Multi-worker uvicorn:** need `QDRANT_URL` (shared), not embedded mode
- **Async ingest:** wrap `parse_workbook` in a Celery/RQ task; return 202 +
  job_id; poll for completion
- **Per-tenant isolation:** prefix all Qdrant collection names with `tenant_id`;
  scope `ChatSessionStore` by tenant
- **Rate limiting:** add `slowapi` middleware on `/chat`
- **Observability:** swap stdout logging for structlog → Datadog
- **Encryption at rest:** use Qdrant Cloud (encrypted by default) or
  volume-level encryption on self-hosted
