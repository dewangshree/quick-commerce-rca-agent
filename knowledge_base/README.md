# Knowledge base (RAG corpus)

- `reference/` — documentation supplied with the original project (metric definition, gold-table schema, RCA playbook).
- `samples/` — **SAMPLE documents written for demonstration only.** They are not real company policies, SLAs or incidents.
  Every file carries `sample: true` front matter, the retriever marks them `is_sample`, and reports label them as samples.

Replace or extend `samples/` with genuine documents, then run `python -m qc_rca.cli ingest-docs`.
This README is not ingested.
