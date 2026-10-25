"""Retention purges old history, audit logs and checkpoints, and keeps recent ones."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from qc_rca.config import get_settings
from qc_rca.db.models import AuditLog, Investigation
from qc_rca.db.session import get_engine, session_scope
from qc_rca.retention import purge_expired


def test_purge_old_keeps_recent(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "investigation_retention_days", 30)
    monkeypatch.setattr(s, "audit_retention_days", 90)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add_all([
            Investigation(id="old-inv", question="q", status="completed", llm_used=False,
                          created_at=now - timedelta(days=31)),
            Investigation(id="new-inv", question="q", status="completed", llm_used=False,
                          created_at=now - timedelta(days=1)),
            AuditLog(action="a", method="GET", path="/x", status_code=200, latency_ms=1, ts=now - timedelta(days=91)),
            AuditLog(action="keep", method="GET", path="/x", status_code=200, latency_ms=1, ts=now),
        ])
    old_ts = (now - timedelta(days=40)).isoformat()
    with get_engine().begin() as c:
        has_cp = c.execute(text("SELECT to_regclass('checkpoints') IS NOT NULL")).scalar()
        if has_cp:
            c.execute(text("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                           "VALUES ('inv-old-inv', '', 'c1', CAST(:cp AS jsonb), '{}'::jsonb)"),
                      {"cp": f'{{"ts": "{old_ts}"}}'})
    out = purge_expired(now)
    assert out["investigations"] >= 1 and out["audit_logs"] >= 1
    with session_scope() as db:
        ids = set(db.scalars(select(Investigation.id)))
        assert "old-inv" not in ids and "new-inv" in ids
        assert db.scalar(select(AuditLog).where(AuditLog.action == "keep")) is not None
    if has_cp:
        assert out["checkpoint_threads"] >= 1
        with get_engine().connect() as c:
            assert c.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id='inv-old-inv'")).scalar() == 0


def test_zero_means_keep_forever(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "investigation_retention_days", 0)
    monkeypatch.setattr(s, "audit_retention_days", 0)
    with session_scope() as db:
        db.add(Investigation(id="ancient", question="q", status="completed", llm_used=False,
                             created_at=datetime.now(timezone.utc) - timedelta(days=3650)))
    assert purge_expired() == {"investigations": 0, "audit_logs": 0, "checkpoint_threads": 0}
    with session_scope() as db:
        assert db.get(Investigation, "ancient") is not None
