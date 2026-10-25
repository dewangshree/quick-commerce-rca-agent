#!/bin/sh
# Build-time: create the cluster, bootstrap roles, migrate and load all data, then stop.
set -e
PGBIN="$(python -c 'import os, pgserver; print(os.path.dirname(pgserver.__file__))')/pginstall/bin"
"$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust -E UTF8 >/dev/null
"$PGBIN/pg_ctl" -D "$PGDATA" -w -o "-p 5432 -c listen_addresses=127.0.0.1" start >/dev/null
"$PGBIN/psql" -q -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 -f scripts/bootstrap_postgres.sql >/dev/null
python -m qc_rca.cli migrate
python -m qc_rca.cli ingest
for f in data/synthetic/*.csv; do python -m qc_rca.cli ingest --path "$f" --synthetic >/dev/null; done
python -m qc_rca.cli ingest-docs --no-embed
"$PGBIN/pg_ctl" -D "$PGDATA" -w -m fast stop >/dev/null
echo "database baked into image"
