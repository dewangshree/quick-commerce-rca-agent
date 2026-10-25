# Quick-Commerce AI Operations Intelligence Platform

**Live demo:** https://qc-ops-intelligence-6bok.onrender.com (free Render instance: the first visit after 15 idle minutes takes about a minute to wake up; the hosted demo runs without the local LLM, so document search is keyword-only).

Evidence-based OR2A SLA analytics and root-cause investigation for quick-commerce delivery operations,
running entirely locally: **PostgreSQL + pgvector, deterministic RCA rules, a LangGraph investigation
workflow, local RAG, an MCP server, and a React dashboard.** No paid API keys or cloud AI services.

> Upgraded from the original SQLite + Groq agent. The original RCA logic is preserved and verified
> line by line against its outputs (see [docs/EVALUATION.md](docs/EVALUATION.md)).

## What it does

* **Dashboards** (React + TS): network/city KPIs, breach rate by hour, city and store comparisons,
  worst stores, filters by city/store/date.
* **AI investigation chat**: multi-turn questions ("Why did STORE_101 underperform?" → "Walk me through
  the morning hours" → "What about STORE_102?" → "Compare them").
* **Structured incident reports**: scope, observed metrics, rule-based contributing factors with evidence
  IDs, document citations, missing data, confidence limitations, next steps and corrective actions,
  plus a downloadable Markdown report and an investigation history.
* **RAG** over the RCA playbook, metric definitions and schema docs, plus clearly labelled **SAMPLE**
  SLA/SOP/incident documents (`knowledge_base/samples/`, not real company information).
* **MCP server** with six read-only tools for external agents.

All numbers come from SQL and deterministic Python. Contributing factors are rule-based co-occurrences,
never presented as proven causes. The local LLM (`qwen2.5:1.5b`) is optional; see
[EVALUATION](docs/EVALUATION.md) for why it is not trusted with routing or calculations.

## Quick start (local)

Requirements: Python 3.11, Node 18+, Ollama (optional). Tested on macOS (Intel), 8 GB RAM.

```bash
# 1. Python env
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# 2. PostgreSQL 16 + pgvector — either your own install, or the bundled pgserver binaries:
PGBIN=$(.venv/bin/python -c "import pgserver,os;print(os.path.dirname(pgserver.__file__))")/pginstall/bin
$PGBIN/initdb -D .pgdata -U postgres --auth=trust -E UTF8
$PGBIN/pg_ctl -D .pgdata -l .pgdata/server.log -o "-p 5432 -c listen_addresses=localhost" start
$PGBIN/psql -h localhost -U postgres -d postgres -f scripts/bootstrap_postgres.sql

# 3. Config, schema, data
cp .env.example .env            # set API_KEY_PEPPER
.venv/bin/python -m qc_rca.cli migrate
.venv/bin/python -m qc_rca.cli ingest          # idempotent; re-runs are skipped
.venv/bin/python -m qc_rca.cli ingest-docs     # embeds with all-minilm if Ollama is running
.venv/bin/python -m qc_rca.cli create-key me analyst   # prints the API key once

# 4. (optional) local models — ~1 GB total
ollama serve &   # then, only if not installed: ollama pull qwen2.5:1.5b && ollama pull all-minilm

# 5. Run
.venv/bin/python main.py                       # API on http://127.0.0.1:8000  (docs: /docs)
cd frontend && npm install && cp .env.example .env && npm run dev   # http://localhost:5173
```

**Synthetic demo days (optional).** The real dataset has a single day (2026-04-22). To demo the date
filter and daily trend, generate clearly labelled synthetic days (seeded variations of the real day):

```bash
.venv/bin/python scripts/generate_synthetic_days.py          # writes data/synthetic/SYNTHETIC_*.csv
for f in data/synthetic/*.csv; do .venv/bin/python -m qc_rca.cli ingest --path "$f" --synthetic; done
```

Rows are stored with `is_synthetic = true`; the UI labels those dates "(synthetic)" with a warning banner,
reports start with a SYNTHETIC DEMO DATA warning and get low confidence, and defaults/baselines use real
dates only.

Paste the API key into **Settings** in the UI. CLI alternative:
`.venv/bin/python -m qc_rca.cli ask "Why did STORE_101 underperform?"`.

Legacy Streamlit UI (kept for now): `pip install streamlit requests`, then
`QC_API_KEY=<key> streamlit run app.py`.

### MCP

```json
{ "mcpServers": { "qc-ops": { "command": "/abs/path/.venv/bin/python",
  "args": ["-m", "qc_rca.mcp_server"], "cwd": "/abs/path/quick-commerce-rca-agent" } } }
```

Tools: `store_analytics`, `rca_analysis`, `city_comparison`, `city_store_summary`, `sla_metrics`,
`knowledge_search` (all read-only, validated, time-limited).

### Tests

```bash
.venv/bin/python -m pytest          # needs the local Postgres; Ollama tests skip if it is down
.venv/bin/python scripts/llm_probe.py   # re-measure the local model's capabilities
```

Current result: **310 passed**.

## API (summary — full OpenAPI at `/docs`)

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health` | public | DB + Ollama status |
| `GET /api/v1/analytics/meta` | viewer | cities, stores, dates |
| `GET /api/v1/analytics/overview?date&city` | viewer | KPIs + breach by hour |
| `GET /api/v1/analytics/cities?date` | viewer | city comparison |
| `GET /api/v1/analytics/cities/{city}/stores?date` | viewer | per-store roll-up |
| `GET /api/v1/analytics/breach-by-hour?date&city&store` | viewer | hourly series |
| `GET /api/v1/analytics/top-stores?date&city&limit&min_orders` | viewer | ranking |
| `GET /api/v1/analytics/stores/compare?stores=A,B&date` | viewer | store comparison |
| `GET /api/v1/analytics/stores/{store}/hours?date&hour_from&hour_to` | viewer | hourly detail |
| `GET /api/v1/analytics/stores/{store}/rca?date` | viewer | deterministic RCA |
| `POST /api/v1/investigations` `{question, thread_id?}` | analyst (rate-limited) | run the LangGraph workflow |
| `GET /api/v1/investigations`, `/{id}`, `/{id}/report.md` | analyst (own) / admin (all) | history and download |
| `POST /api/v1/knowledge/search` | analyst | hybrid retrieval with filters |
| `POST /api/v1/admin/ingest`, `GET /api/v1/admin/audit` | admin | ingestion, audit log |
| `POST /chat` | analyst | legacy Streamlit compatibility |

## Storage and resources (measured)

| Item | Size |
|---|---|
| Python venv (lean) | 187 MB |
| `pgserver` (PostgreSQL 16.2 + pgvector binaries) | ~10 MB wheel |
| Postgres data dir (`.pgdata`, both databases) | 113 MB |
| `frontend/node_modules` | 90 MB |
| Ollama models: qwen2.5:1.5b + all-minilm | 986 MB + 45 MB |

Idle RAM: Postgres ~50 MB with `shared_buffers=64MB`; Ollama loads models only on demand (unloads after 5 min).

## Documentation

* [Architecture, workflow, RAG and database diagrams](docs/ARCHITECTURE.md)
* [Security, failure handling, scaling, architecture decisions](docs/OPERATIONS.md)
* [Evaluation report (actual results)](docs/EVALUATION.md)
* Reference docs used by RAG: [knowledge_base/](knowledge_base/)

## Docker

```bash
export API_KEY_PEPPER=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
docker compose up --build -d                      # db + api (:8000) + web (:8080)
docker compose exec api python -m qc_rca.cli create-key me analyst
```

* `db`: `pgvector/pgvector:pg16`, bootstrapped by `scripts/bootstrap_postgres.sql` (roles + databases).
* `api`: migrates, ingests the CSV and knowledge base (idempotent), then serves. Defaults to
  `ENV=production` (auth required, `/docs` off). `LOAD_SYNTHETIC=true` also loads the demo days.
* `web`: production React build behind nginx; Settings hidden; set `PUBLIC_API_URL` to the URL
  browsers use to reach the API.
* Ollama is not containerised; the API uses `host.docker.internal:11434` if it is running, and
  works without it.

Approximate image downloads: postgres+pgvector ~150 MB, python slim ~50 MB, node + nginx ~60 MB
(compressed), plus Python dependencies.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests:

1. **backend**: pytest against a real `pgvector/pgvector:pg16` service (Ollama-dependent tests skip).
2. **frontend**: `npm ci`, typecheck, production build.
3. **docker**: builds both images, starts `db` + `api` with Compose, checks `/health` and that
   unauthenticated requests get 401.

## Deployment

The public demo runs on Render's free web service from `render.yaml` and `deploy/Dockerfile.render`:
one container with Postgres + pgvector, the API and the React build. The database is created and
loaded at image build time, so cold starts are quick and every restart returns to the same data
(investigation history is reset on restart). `PUBLIC_DEMO=true` lets visitors use dashboards and
investigations without a key (per-browser history, per-IP rate limits); admin endpoints still need
a key. CI builds this image and smoke-tests it at 512 MB (about 130 MB used).

Free-tier limits (checked October 2026): sleeps after 15 minutes idle, about 1 minute to wake,
no room for Ollama. The same image runs on any Docker host if the free tier changes.
