"""Admin endpoints: ingestion and audit log (role: admin)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from qc_rca.config import get_settings
from qc_rca.db.models import AuditLog, Role
from qc_rca.db.session import session_scope
from qc_rca.ingestion import ingest_csv
from qc_rca.rag.store import ingest_documents
from qc_rca.security.auth import Principal, require_role

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
Admin = Annotated[Principal, Depends(require_role(Role.admin))]


@router.post("/ingest")
def ingest(_: Admin, force: bool = False):
    """Re-ingest the configured CSV and knowledge base (idempotent). Paths are server-side config only."""
    s = get_settings()
    with session_scope() as session:
        csv_result = ingest_csv(session, s.data_csv_path, force=force)
    with session_scope() as session:
        doc_result = ingest_documents(session)
    return {"orders": csv_result.model_dump(), "documents": doc_result}


@router.get("/audit")
def audit(_: Admin, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).all()
        return [{"ts": r.ts, "user": r.user_name, "role": r.role, "action": r.action, "method": r.method,
                 "path": r.path, "status": r.status_code, "latency_ms": r.latency_ms, "details": r.details}
                for r in rows]
