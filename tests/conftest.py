"""Test fixtures: a dedicated PostgreSQL database (qc_rca_test), migrated and loaded once per session.

Set TEST_DATABASE_URL / TEST_READONLY_DATABASE_URL to override. Tests that need
Ollama are skipped when it is not reachable; everything else must pass without it.
"""
import os

import pytest

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://qc_app:qc_app_dev@localhost:5432/qc_rca_test")
os.environ["READONLY_DATABASE_URL"] = os.getenv(
    "TEST_READONLY_DATABASE_URL", "postgresql+psycopg://qc_readonly:qc_readonly_dev@localhost:5432/qc_rca_test")
os.environ.setdefault("API_KEY_PEPPER", "test-pepper")
os.environ["CHECKPOINT_ENABLED"] = os.getenv("TEST_CHECKPOINT_ENABLED", "true")
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["AUTH_ENABLED"] = "true"  # tests exercise auth even if .env disables it

from sqlalchemy import text  # noqa: E402

from qc_rca.config import ROOT_DIR, get_settings  # noqa: E402
from qc_rca.db.session import get_engine, reset_engines, session_scope  # noqa: E402
from qc_rca.llm.ollama import ollama_status  # noqa: E402

get_settings.cache_clear()


def _ollama_ok() -> bool:
    st = ollama_status()
    return st["reachable"] and st["embedding_installed"]


OLLAMA_OK = _ollama_ok()
requires_ollama = pytest.mark.skipif(not OLLAMA_OK, reason="Ollama / embedding model not available")


@pytest.fixture(scope="session", autouse=True)
def database():
    from alembic import command
    from alembic.config import Config

    from qc_rca.ingestion import ingest_csv
    from qc_rca.rag.store import ingest_documents

    reset_engines()
    eng = get_engine()
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    command.downgrade(cfg, "base")  # keeps the superuser-created vector extension
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        c.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO qc_readonly"))
    with session_scope() as s:
        ingest_csv(s, get_settings().data_csv_path)
    with session_scope() as s:
        ingest_documents(s, with_embeddings=OLLAMA_OK)
    yield
    reset_engines()


@pytest.fixture
def api_keys():
    from qc_rca.db.models import Role
    from qc_rca.security.auth import create_api_user

    with session_scope() as s:
        keys = {r.value: create_api_user(s, f"test-{r.value}", r) for r in Role}
    return keys


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from qc_rca.api.main import create_app
    from qc_rca.security.auth import get_rate_limiter

    get_rate_limiter().reset()
    return TestClient(create_app())
