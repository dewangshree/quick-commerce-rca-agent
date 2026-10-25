"""Retention: delete old investigation history, audit logs and LangGraph checkpoints."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, inspect, text

from qc_rca.config import get_settings
from qc_rca.db.models import AuditLog, Investigation
from qc_rca.db.session import get_engine, session_scope
from qc_rca.observability import get_logger, log_event

log = get_logger("retention")
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def _purge_checkpoints(cutoff: datetime) -> int:
    """Delete checkpoint threads whose newest checkpoint is older than the cutoff."""
    engine = get_engine()
    if not inspect(engine).has_table("checkpoints"):
        return 0
    with engine.begin() as c:
        threads = list(c.scalars(text(
            "SELECT thread_id FROM checkpoints GROUP BY thread_id "
            "HAVING max((checkpoint->>'ts')::timestamptz) < :cutoff"), {"cutoff": cutoff}))
        if threads:
            for table in CHECKPOINT_TABLES:
                c.execute(text(f"DELETE FROM {table} WHERE thread_id = ANY(:t)"), {"t": threads})
    return len(threads)


def purge_expired(now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    out = {"investigations": 0, "audit_logs": 0, "checkpoint_threads": 0}
    with session_scope() as session:
        if s.investigation_retention_days:
            cutoff = now - timedelta(days=s.investigation_retention_days)
            out["investigations"] = session.execute(
                delete(Investigation).where(Investigation.created_at < cutoff)).rowcount
        if s.audit_retention_days:
            cutoff = now - timedelta(days=s.audit_retention_days)
            out["audit_logs"] = session.execute(delete(AuditLog).where(AuditLog.ts < cutoff)).rowcount
    if s.investigation_retention_days:
        out["checkpoint_threads"] = _purge_checkpoints(now - timedelta(days=s.investigation_retention_days))
    log_event(log, "retention purge", **out)
    return out
