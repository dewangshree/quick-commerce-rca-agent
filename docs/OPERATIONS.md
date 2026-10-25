# Security, failure handling, scaling and decisions

## Security

| Control | Implementation |
|---|---|
| Authentication | `X-API-Key`; random 32-byte keys, only HMAC-SHA256 (keyed by `API_KEY_PEPPER`) stored |
| Authorisation | Roles `viewer` < `analyst` < `admin`. Viewer: analytics/dashboards. Analyst: + investigations, knowledge search, own history. Admin: + all history, ingestion, audit log |
| SQL safety | No free-form or LLM-generated SQL. Allowlisted operations in `analytics/service.py` built with SQLAlchemy Core bound parameters; the only `text()` SQL (RAG) uses bound params |
| Read-only access | Analytics, workflow, RAG and MCP use a separate engine with `default_transaction_read_only=on`; locally it also connects as the SELECT-only `qc_readonly` role (tested: writes fail) |
| Request validation | Pydantic bodies, constrained path/query params (patterns, ranges, lengths); question validator rejects control chars, over-length text, prompt-injection/SQL patterns |
| Errors | Generic 500 body with request ID; stack traces only in server logs; MCP tools return `{"error"}` without internals |
| Secrets | `.env` (git-ignored), `.env.example` without secrets; no paid API keys exist |
| Audit | Every `/api/*` and `/chat` request → `audit_logs` (user, role, action, status, latency, IP, investigation ID) |
| Rate limiting | Per-user sliding window on investigation endpoints (`RATE_LIMIT_PER_MINUTE`) |
| CORS | Explicit origin allowlist (`CORS_ORIGINS`) |
| Multi-tenancy | Not introduced (single organisation). If added: `tenant_id` on business tables + Postgres row-level security keyed by a session setting |

Known gaps: the rate limiter is in-process (use Redis or a gateway for multiple instances); API keys
have no expiry (rotate with `cli create-key NAME ROLE`, which replaces the hash); local passwords in
`scripts/bootstrap_postgres.sql` are dev defaults.

## Failure handling

| Failure | Behaviour (tested where marked ✓) |
|---|---|
| Ollama down / model missing | ✓ Report still completes; narrative skipped with a note; ambiguous questions → clarification |
| Embedding model unavailable | ✓ Retrieval switches to keyword-only and the report lists the limitation |
| LLM returns invalid JSON / invalid intent | Rejected; rules/clarification used |
| LLM narrative contains numbers not in evidence | Retried once with feedback, then discarded (deterministic report only) |
| Unknown store/city | ✓ "Unknown store" + close-match suggestions; no metrics or factors produced |
| Missing source values (man_hour, avg_or2a) | ✓ Check marked UNKNOWN, listed under missing data, confidence lowered |
| Invalid CSV | ✓ Whole file rejected, nothing written, failed `ingestion_runs` row |
| Re-ingesting same CSV | ✓ Skipped by SHA-256; `--force` upserts without duplicates |
| Slow query | Postgres `statement_timeout` (default 5 s) |
| Slow MCP tool | ✓ `MCP_TOOL_TIMEOUT_S` returns an error, worker thread abandoned |
| Node exception in workflow | Caught per node; report status `partial` with error list |
| Audit write failure | Logged; request still served |

## Scaling notes

The current dataset is 3,813 rows for one date; everything runs in one process on a laptop.
Before scaling: partition `store_hourly_metrics` by `charge_date`, add materialised daily store
roll-ups, run several uvicorn workers behind a proxy with a shared rate limiter, and move
embedding into a background job. Postgres + pgvector handles far more than this corpus (45 chunks);
a separate vector database is not justified.

## Architecture decisions

1. **Deterministic-first; LLM optional.** The local 1.5B model was probed before use
   (`docs/EVALUATION.md`): 62.5% intent accuracy, 67% tool-call accuracy, 0% exact structured
   extraction; narratives kept evidence numbers but mislabelled metrics. So routing, entity extraction, calculations and reports
   are deterministic; the LLM is only an enum-validated intent fallback and an optional,
   number-checked narrative (off by default).
2. **PostgreSQL + pgvector over Chroma.** One database for business data, app data, vectors and
   checkpoints; transactional ingestion; metadata filters and full-text search in SQL; no extra
   ~300 MB dependency. Chroma was considered and not added (see `rag/store.py` for the single
   retrieval interface if it is ever needed).
3. **No free-form SQL tool.** The original agent let the LLM write SQL against the DB. It was
   replaced by allowlisted operations.
4. **Modular monolith.** API, workflow and MCP share one package; no microservices or queues.
5. **Own MCP server instead of the filesystem server.** The original exposed arbitrary file reads
   through `npx`; the new server exposes six typed, read-only tools.
6. **Local Postgres via `pgserver` binaries.** Homebrew had no Intel Sonoma bottles and was
   compiling from source; the `pgserver` wheel (10 MB) ships PostgreSQL 16.2 + pgvector 0.6.2.
   Any PostgreSQL ≥ 15 with pgvector ≥ 0.5 works.
