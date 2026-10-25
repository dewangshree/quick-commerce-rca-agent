#!/bin/sh
# Migrate, load data (idempotent: unchanged files are skipped), then start the API.
set -e
python -m qc_rca.cli migrate
python -m qc_rca.cli ingest
if [ "${LOAD_SYNTHETIC:-false}" = "true" ]; then
  for f in data/synthetic/*.csv; do python -m qc_rca.cli ingest --path "$f" --synthetic; done
fi
python -m qc_rca.cli ingest-docs || echo "document ingestion failed; continuing (keyword search may be empty)"
exec python main.py
