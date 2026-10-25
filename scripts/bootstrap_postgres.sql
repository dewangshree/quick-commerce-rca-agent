-- One-time local bootstrap (run as a superuser, e.g. `psql -d postgres -f scripts/bootstrap_postgres.sql`).
-- Creates the application role, a SELECT-only analytics role, and the databases.
-- Passwords here are LOCAL DEVELOPMENT defaults; change them for any shared environment.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'qc_app') THEN
    CREATE ROLE qc_app LOGIN PASSWORD 'qc_app_dev';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'qc_readonly') THEN
    CREATE ROLE qc_readonly LOGIN PASSWORD 'qc_readonly_dev';
  END IF;
END
$$;

SELECT 'CREATE DATABASE qc_rca OWNER qc_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'qc_rca') \gexec
SELECT 'CREATE DATABASE qc_rca_test OWNER qc_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'qc_rca_test') \gexec

-- pgvector must be created by a superuser (or a trusted-extension owner).
\connect qc_rca
CREATE EXTENSION IF NOT EXISTS vector;
GRANT CONNECT ON DATABASE qc_rca TO qc_readonly;
GRANT USAGE ON SCHEMA public TO qc_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE qc_app IN SCHEMA public GRANT SELECT ON TABLES TO qc_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO qc_readonly;
ALTER ROLE qc_readonly SET default_transaction_read_only = on;

\connect qc_rca_test
CREATE EXTENSION IF NOT EXISTS vector;
GRANT CONNECT ON DATABASE qc_rca_test TO qc_readonly;
GRANT USAGE ON SCHEMA public TO qc_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE qc_app IN SCHEMA public GRANT SELECT ON TABLES TO qc_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO qc_readonly;
