"""Engines and sessions.

Two engines:
* ``get_engine()``          read/write, used for ingestion and app tables.
* ``get_readonly_engine()`` used by analytics, the LangGraph workflow and MCP
  tools. Every connection is forced into ``default_transaction_read_only`` with
  a ``statement_timeout``, so even the same DB role cannot write through it.
  In production point ``READONLY_DATABASE_URL`` at a SELECT-only role as well
  (see scripts/bootstrap_postgres.sql).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from qc_rca.config import get_settings


def _pg_options(read_only: bool) -> dict:
    s = get_settings()
    opts = f"-c statement_timeout={s.db_statement_timeout_ms}"
    if read_only:
        opts += " -c default_transaction_read_only=on"
    return {"options": opts, "connect_timeout": 5}


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url, pool_size=s.db_pool_size, pool_pre_ping=True, connect_args=_pg_options(False)
    )


@lru_cache
def get_readonly_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.analytics_database_url, pool_size=s.db_pool_size, pool_pre_ping=True, connect_args=_pg_options(True)
    )


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on any error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engines() -> None:
    """Used by tests after changing settings."""
    for fn in (get_engine, get_readonly_engine):
        if fn.cache_info().currsize:
            fn().dispose()
        fn.cache_clear()
    _session_factory.cache_clear()
