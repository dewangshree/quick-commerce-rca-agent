# Quick-Commerce AI Operations Intelligence Platform

An AI investigation platform for quick-commerce delivery operations. Ask *"Why did STORE_101 underperform?"* and it pulls the numbers with SQL, runs deterministic root-cause rules, searches the operations playbook with hybrid RAG, and returns a structured, cited incident report. Operations teams also get a live dashboard of OR2A SLA breaches across cities and stores.

Everything runs locally: no paid API keys or cloud AI services.

**Live demo:** https://qc-ops-intelligence-6bok.onrender.com

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?logo=langchain&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16_+_pgvector-4169E1?logo=postgresql&logoColor=white)
![React](https://img.shields.io/badge/React-TypeScript-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-310_passing-brightgreen)

---

## Try the demo

The demo runs on Render's free tier, so the first visit after 15 idle minutes takes about a minute to wake up. Public demo mode is on: dashboards and investigations work **without an API key** (admin endpoints still need one).

Try these questions in the investigation chat, in order:

| # | Question |
|---|----------|
| 1 | `Why did STORE_101 underperform?` |
| 2 | `Walk me through the morning hours` |
| 3 | `What about STORE_102?` |
| 4 | `Compare them` |

The hosted demo runs the all-MiniLM embedding model in-process for hybrid search, but not the Qwen LLM, which needs more than the free 512 MB. Every number and finding still works, because none of them come from the LLM.

---

## Features

### Operations dashboard
- **KPIs:** network and city-level orders, breaches and breach rate
- **Charts:** breach rate by hour, city comparison, store comparison and worst stores
- **Filters:** city, store and date (synthetic demo dates are clearly labelled)

### AI investigation chat
- **Multi-turn questions:** follow-ups reuse the store, city and date from earlier in the thread
- **Structured incident reports:** scope, observed metrics, rule-based contributing factors with evidence IDs, document citations, missing data, confidence limitations, next steps and corrective actions
- **History and export:** every investigation is saved and can be downloaded as Markdown

### Root-cause rules
Every problem hour is checked against all three rules independently, because causes can compound:

| Check | Rule | Flag |
|-------|------|------|
| Demand | Actual orders > forecast × 1.10 | Demand spike |
| Pileup | Orders carried over from the previous hour; 3+ consecutive hours is sustained | Pileup / sustained pileup |
| Supply: booking | Booked rider slots / available slots < 0.90 | Booking gap |
| Supply: utilization | Man-hour ratio < 0.85, with no-show count | Utilization gap |

All numbers come from SQL and deterministic Python. Contributing factors are rule-based co-occurrences, never presented as proven causes.

### Knowledge search (RAG)
- **Sources:** the RCA playbook, metric definitions and schema docs, plus clearly labelled **SAMPLE** SLA, SOP and incident documents
- **Hybrid retrieval:** pgvector semantic search and PostgreSQL full-text search, merged with reciprocal rank fusion
- **Citations:** every report cites the document chunks it used

### Platform
- **LangGraph workflow:** validate → classify → analytics → retrieve → correlate → verify → report, with typed state, bounded retries and timeouts
- **Self-checking:** a verification step recomputes every flag from the raw rows and checks every citation before the report is returned
- **Works without the LLM:** the local model (`qwen2.5:1.5b`) is optional and only used when rules can't classify a question; it is never trusted with routing or calculations
- **MCP server:** six read-only tools so external agents (such as Claude Desktop) can query the analytics
- **Security:** hashed API keys, role-based access (`viewer` / `analyst` / `admin`), rate limiting, audit logs and request IDs
- **Tested:** 310 passing tests and CI on every push

> Upgraded from the original SQLite + Groq agent. The original RCA logic is preserved and verified line by line against its outputs.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, FastAPI, SQLAlchemy, Pydantic, Alembic |
| AI workflow | LangGraph (Postgres checkpoints), LangChain, Ollama (`qwen2.5:1.5b`) |
| Retrieval | pgvector (HNSW), PostgreSQL full-text search (GIN), all-MiniLM embeddings via Ollama or fastembed (ONNX) |
| Database | PostgreSQL 16 + pgvector |
| Frontend | React, TypeScript, Vite |
| Agent integration | MCP server |
| Hosting | Render (single Docker container) |
| CI/CD | GitHub Actions (backend tests, frontend build, Docker smoke test) |

---

## Project structure

```
quick-commerce-rca-agent/
├── qc_rca/                       # Backend package
│   ├── api/                      # FastAPI app
│   │   └── routes/               #   analytics, investigations, knowledge, admin, legacy
│   ├── workflow/                 # LangGraph graph, intent rules, evidence & validation
│   ├── analytics/
│   │   ├── rca.py                #   deterministic RCA rules
│   │   └── service.py            #   allowlisted, parameterised SQL operations
│   ├── rag/                      # Chunking, document store, hybrid retrieval
│   ├── llm/ollama.py             # Local model access and availability checks
│   ├── db/                       # ORM models, read-write and read-only engines
│   ├── security/auth.py          # API keys, RBAC, rate limiting
│   ├── ingestion.py              # Validated, idempotent CSV upsert
│   ├── mcp_server.py             # Read-only MCP tools
│   └── cli.py                    # migrate, ingest, ingest-docs, create-key, ask
├── frontend/                     # React + TypeScript dashboard
├── knowledge_base/
│   ├── reference/                # RCA playbook, OR2A definition, table schema
│   └── samples/                  # SAMPLE SLA, SOP and incident docs
├── data/                         # Real dataset + synthetic/ demo days
├── migrations/                   # Alembic migrations
├── tests/                        # pytest suite (310 tests)
├── scripts/                      # Postgres bootstrap, synthetic data, LLM probe
├── deploy/                       # Render Dockerfile and start scripts
├── docs/                         # Architecture, operations, evaluation
├── docker-compose.yml
└── .github/workflows/ci.yml
```

---

## Getting started (local)

**Prerequisites:** Python 3.11, Node.js 18+, and optionally Ollama. Tested on macOS (Intel) with 8 GB RAM.

### 1. Python environment

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

### 2. PostgreSQL 16 + pgvector

Use your own install, or the bundled `pgserver` binaries:

```bash
PGBIN=$(.venv/bin/python -c "import pgserver,os;print(os.path.dirname(pgserver.__file__))")/pginstall/bin
$PGBIN/initdb -D .pgdata -U postgres --auth=trust -E UTF8
$PGBIN/pg_ctl -D .pgdata -l .pgdata/server.log -o "-p 5432 -c listen_addresses=localhost" start
$PGBIN/psql -h localhost -U postgres -d postgres -f scripts/bootstrap_postgres.sql
```

### 3. Config, schema and data

```bash
cp .env.example .env                                   # set API_KEY_PEPPER
.venv/bin/python -m qc_rca.cli migrate                 # create tables
.venv/bin/python -m qc_rca.cli ingest                  # load the CSV (skips if already loaded)
.venv/bin/python -m qc_rca.cli ingest-docs             # chunk + embed the knowledge base
.venv/bin/python -m qc_rca.cli create-key me analyst   # prints the API key once
```

### 4. Local models (optional, ~1 GB)

```bash
ollama serve &
ollama pull qwen2.5:1.5b && ollama pull all-minilm
```

### 5. Run

```bash
.venv/bin/python main.py                               # API → http://127.0.0.1:8000 (docs at /docs)
cd frontend && npm install && cp .env.example .env && npm run dev   # UI → http://localhost:5173
```

Paste the API key into **Settings** in the UI, or ask from the terminal:

```bash
.venv/bin/python -m qc_rca.cli ask "Why did STORE_101 underperform?"
```

### Docker (alternative)

```bash
export API_KEY_PEPPER=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
docker compose up --build -d                           # db + api (:8000) + web (:8080)
docker compose exec api python -m qc_rca.cli create-key me analyst
```

| Service | What it does |
|---------|--------------|
| `db` | `pgvector/pgvector:pg16`, bootstrapped with roles and databases |
| `api` | Migrates, loads the CSV and knowledge base, then serves (production mode: auth required, `/docs` off) |
| `web` | Production React build behind nginx |

Ollama is not containerised: the API uses `host.docker.internal:11434` if it is running, and works without it.

---

## API reference

Full OpenAPI docs at `/docs`. Authenticated routes need the `X-API-Key` header.

| Method | Endpoint | Access | Description |
|--------|----------|--------|-------------|
| GET | `/health` | Public | Database + Ollama status |
| GET | `/api/v1/analytics/meta` | Viewer | Cities, stores, dates |
| GET | `/api/v1/analytics/overview?date&city` | Viewer | KPIs + breach rate by hour |
| GET | `/api/v1/analytics/cities?date` | Viewer | City comparison |
| GET | `/api/v1/analytics/cities/{city}/stores?date` | Viewer | Per-store roll-up |
| GET | `/api/v1/analytics/breach-by-hour?date&city&store` | Viewer | Hourly series |
| GET | `/api/v1/analytics/top-stores?date&city&limit&min_orders` | Viewer | Store ranking |
| GET | `/api/v1/analytics/stores/compare?stores=A,B&date` | Viewer | Compare stores |
| GET | `/api/v1/analytics/stores/{store}/hours?date&hour_from&hour_to` | Viewer | Hourly detail |
| GET | `/api/v1/analytics/stores/{store}/rca?date` | Viewer | Deterministic RCA |
| POST | `/api/v1/investigations` | Analyst (rate-limited) | Run the LangGraph investigation (`question`, optional `thread_id`) |
| GET | `/api/v1/investigations` · `/{id}` · `/{id}/report.md` | Analyst (own) / Admin (all) | History and Markdown download |
| POST | `/api/v1/knowledge/search` | Analyst | Hybrid retrieval with filters |
| POST | `/api/v1/admin/ingest` | Admin | Load data |
| GET | `/api/v1/admin/audit` | Admin | Audit log |
| POST | `/chat` | Analyst | Legacy Streamlit compatibility |

### MCP server

```json
{
  "mcpServers": {
    "qc-ops": {
      "command": "/abs/path/.venv/bin/python",
      "args": ["-m", "qc_rca.mcp_server"],
      "cwd": "/abs/path/quick-commerce-rca-agent"
    }
  }
}
```

Tools: `store_analytics`, `rca_analysis`, `city_comparison`, `city_store_summary`, `sla_metrics`, `knowledge_search`. All are read-only, validated and time-limited.

---

## Testing

```bash
.venv/bin/python -m pytest              # needs local Postgres; Ollama tests skip if it is down
.venv/bin/python scripts/llm_probe.py   # re-measure the local model's capabilities
```

**Current result: 310 passed.**

---

## Deployment

| Part | Where | How |
|------|-------|-----|
| App (API + React + Postgres) | Render free web service | `render.yaml` + `deploy/Dockerfile.render` build one container; the database is created and loaded at image build time, so restarts return to the same data |
| Embeddings | Inside the container | `EMBEDDING_BACKEND=fastembed` runs all-MiniLM-L6-v2 via ONNX, so hybrid search works without Ollama |
| CI | GitHub Actions | On every push to `main` and on PRs: backend pytest against real pgvector, frontend type-check + build, Docker build + `/health` and 401 checks |

CI smoke-tests the Render image at 512 MB (about 270 MB used). The same image runs on any Docker host.

---

## Sample data

`qc_rca.cli ingest` loads:
- **3,813 store-hour rows** for 22 April 2026, covering **233 stores** across **9 cities**
- rider-slot capacity normalised into its own table (verified constant per store, date and slot)

`qc_rca.cli ingest-docs` loads:
- 3 reference docs: the RCA playbook, the OR2A definition and the table schema
- 5 **SAMPLE** docs: SLA definitions, 2 SOPs and 2 incident write-ups (not real company information)

**Synthetic demo days (optional).** The real dataset covers one day. To demo the date filter and daily trend:

```bash
.venv/bin/python scripts/generate_synthetic_days.py
for f in data/synthetic/*.csv; do .venv/bin/python -m qc_rca.cli ingest --path "$f" --synthetic; done
```

This adds 6 seeded variations of the real day, stored with `is_synthetic = true`. The UI labels them "(synthetic)" with a warning banner, reports start with a **SYNTHETIC DEMO DATA** warning and get low confidence, and defaults and baselines use real dates only.

---

## Known limitations

- **One real day of data.** Trends across dates rely on clearly labelled synthetic days.
- **Factors are co-occurrences, not proven causes.** The rules show what happened alongside the breaches; they don't establish causation.
- **Small local LLM.** `qwen2.5:1.5b` is too unreliable for routing or calculations, so it is only a fallback for classification and narrative; see [docs/EVALUATION.md](docs/EVALUATION.md).
- **No LLM on the hosted demo.** Render's free 512 MB can't fit Qwen, and investigation history resets when the instance restarts.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md): system, workflow, RAG and database diagrams
- [Operations](docs/OPERATIONS.md): security, failure handling, scaling and architecture decisions
- [Evaluation](docs/EVALUATION.md): measured results
- [Knowledge base](knowledge_base/): documents used by RAG
