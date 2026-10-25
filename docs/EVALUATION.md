# Evaluation report

All figures below come from runs on 2026-10-25 on an Intel MacBook Air (8 GB RAM),
PostgreSQL 16.2 + pgvector 0.6.2, Ollama with `qwen2.5:1.5b` and `all-minilm`.
Nothing here is an estimated or invented score. Raw outputs: `docs/evaluation/*.json`.

## Automated tests — `pytest`: **310 passed** (41.7 s)

| File | What it covers | Tests |
|---|---|---|
| `test_rca_regression.py` | New Postgres implementation vs the **original SQLite code's outputs** for all 233 stores and 9 cities | 244 |
| `test_rca_rules.py` | RCA thresholds, sustained pileup, missing data, weighting | 8 |
| `test_ingestion.py` | Counts, idempotency (skip + forced upsert), invalid file rollback, DB constraints | 8 |
| `test_workflow.py` | 14 regression questions → intent, evidence-based RCA report, multi-turn context, injection rejection, model-unavailable degradation, grounding check | 27 |
| `test_api.py` | Auth 401, RBAC 403, validation 422, analytics, investigations, history visibility, report download, legacy `/chat`, rate limit 429, audit log, read-only engine | 11 |
| `test_rag.py` | Chunking, metadata filters, sample exclusion, keyword & hybrid retrieval eval, keyword fallback | 6 |
| `test_mcp.py` | Tool listing + read-only annotations, tool calls, invalid inputs, timeout, **real stdio transport** | 6 |

## Regression against the original RCA

The golden outputs (`tests/fixtures/golden_original_outputs.json`) were produced by running the
original `tools.run_rca` / `get_city_summary` from git HEAD on the original data.

* **232 of 233 stores**: every line of the RCA report is identical, except lines for hours where
  `man_hour` is missing.
* **108 problem hours** had missing `man_hour` (booked minutes = 0). The original coerced it to 0 and
  reported a false **utilization gap**; the new engine reports the check as not evaluated and lists it
  under missing data. The test asserts that this is the *only* difference.
* **STORE_008**: the original crashed (`TypeError`, null `avg_or2a`); it now completes, excluding those
  hours from the weighted OR2A and reporting them.
* City summaries: identical order counts, breach rates and problem hours for all stores; weighted OR2A
  identical to 1 dp except STORE_008 (same null handling).
* `Mumbai north` now resolves (the source city is `"Mumbai  north"` with two spaces; the original exact
  match failed).

## RAG retrieval (10 representative questions, k = 4)

| Mode | hit@4 | Questions with expected doc at rank 1 |
|---|---|---|
| Keyword only (Postgres full-text) | 10/10 | 7/10 |
| Hybrid (all-minilm vectors + full-text, RRF) | 10/10 | 9/10 |

The corpus is small (8 documents, 45 chunks), so this mainly shows that the pipeline works; it does
not predict quality on a large corpus.

## Local LLM capability probe (`scripts/llm_probe.py`, qwen2.5:1.5b, temperature 0)

| Capability | Result | Decision |
|---|---|---|
| Intent classification (JSON mode), 16 questions | 62.5% accuracy, 100% valid enum, median 5.6 s | Rules are primary (14/14 on the regression set); LLM only for questions the rules can't classify, output enum-validated |
| Tool calling (`bind_tools`), 6 prompts | 4/6 right tool + exact args; 2/6 made no tool call | Not used for routing |
| Structured output (`with_structured_output`), 4 prompts | 4/4 schema-valid, **0/4** correct (e.g. date put into `city`, `"false"` as a store) | Not used; regex entity extraction instead |
| Grounded narrative, 4 samples | Numbers grounded in 4/4 (after fixing a checker bug that read `-22` from a date); but it called OR2A "order fulfillment time" | Narrative **off by default** (`LLM_NARRATIVE_ENABLED`); when on, it must pass the number check |
| Embeddings (all-minilm) | 384-d, ~0.9 s for 2 texts | Used for hybrid retrieval |

The probe ran while a Homebrew compile was using the CPU, so latencies are pessimistic.

## Latency observed (live server, deterministic path)

* Store RCA investigation with retrieval: ~1.75 s (includes query embedding).
* Follow-up hourly drill-down in the same thread: ~0.4 s.
* Analytics endpoints: tens of milliseconds on this dataset.

## Not verified

* The React UI was type-checked, built and served (HTTP 200, CORS verified), but **not exercised in a
  browser** during this session.
* The legacy Streamlit UI was updated but not run (Streamlit is not installed in the lean venv).
* Docker / CI: not created yet (deferred on request).
