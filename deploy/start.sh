#!/bin/sh
# Runtime: start the baked Postgres (low-memory settings), then the API on $PORT.
set -e
PGBIN="$(python -c 'import os, pgserver; print(os.path.dirname(pgserver.__file__))')/pginstall/bin"
rm -f "$PGDATA/postmaster.pid"   # stale after an unclean stop
"$PGBIN/pg_ctl" -D "$PGDATA" -w -l /tmp/postgres.log \
  -o "-p 5432 -c listen_addresses=127.0.0.1 -c shared_buffers=32MB -c max_connections=20 -c work_mem=4MB" start
export PORT="${PORT:-10000}"
exec python main.py
