"""AI investigations (role: analyst+, rate limited) and investigation history."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from qc_rca.config import get_settings
from qc_rca.db.models import Investigation, Role
from qc_rca.db.session import session_scope
from qc_rca.reporting import report_to_markdown
from qc_rca.security.auth import ROLE_ORDER, Principal, rate_limited, require_role
from qc_rca.workflow.graph import run_investigation

router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"])


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=get_settings().max_question_chars)
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


def thread_history(thread_id: str | None, user: str, limit: int = 6) -> list[dict]:
    if not thread_id:
        return []
    with session_scope() as s:
        rows = s.execute(
            select(Investigation.question, Investigation.report).where(
                Investigation.thread_id == thread_id, Investigation.user_name == user
            ).order_by(Investigation.created_at.desc()).limit(limit)
        ).all()
    history = []
    for q, rep in reversed(rows):
        history.append({"role": "user", "content": q})
        if rep:
            history.append({"role": "assistant", "content": rep.get("answer_markdown", "")[:500]})
    return history


def execute_and_store(question: str, user: str, thread_id: str | None, history: list[dict]) -> dict:
    report = run_investigation(question, history=history, thread_id=thread_id)
    with session_scope() as s:
        s.add(Investigation(
            id=report["investigation_id"], user_name=user, thread_id=thread_id, question=question,
            intent=report["intent"], status=report["status"], llm_model=report["llm"].get("model"),
            llm_used=report["llm"]["used"], latency_ms=report["timings_ms"].get("total"), report=report,
            metrics={"timings_ms": report["timings_ms"], "llm_usage": report["llm"].get("usage", [])},
            error="; ".join(report.get("errors", []))[:2000] or None,
        ))
    return report


@router.post("")
async def create_investigation(body: InvestigationRequest, request: Request,
                               principal: Annotated[Principal, Depends(rate_limited(Role.analyst))]):
    history = await run_in_threadpool(thread_history, body.thread_id, principal.name)
    report = await run_in_threadpool(execute_and_store, body.question, principal.name, body.thread_id, history)
    request.state.audit_action = "investigation"
    request.state.audit_details = {"investigation_id": report["investigation_id"], "intent": report["intent"],
                                   "status": report["status"]}
    return report


def _visible(q, principal: Principal):
    if ROLE_ORDER[principal.role] < ROLE_ORDER[Role.admin]:
        q = q.where(Investigation.user_name == principal.name)
    return q


@router.get("")
def list_investigations(principal: Annotated[Principal, Depends(require_role(Role.analyst))],
                        limit: Annotated[int, Query(ge=1, le=200)] = 50):
    q = _visible(select(Investigation.id, Investigation.created_at, Investigation.user_name, Investigation.question,
                        Investigation.intent, Investigation.status, Investigation.latency_ms,
                        Investigation.llm_used, Investigation.thread_id), principal)
    with session_scope() as s:
        rows = s.execute(q.order_by(Investigation.created_at.desc()).limit(limit)).all()
    return [dict(r._mapping) for r in rows]


def _get(inv_id: str, principal: Principal) -> Investigation:
    with session_scope() as s:
        inv = s.scalar(_visible(select(Investigation).where(Investigation.id == inv_id[:36]), principal))
    if inv is None:
        raise HTTPException(404, "Investigation not found")
    return inv


@router.get("/{inv_id}")
def get_investigation(inv_id: str, principal: Annotated[Principal, Depends(require_role(Role.analyst))]):
    inv = _get(inv_id, principal)
    return {"id": inv.id, "created_at": inv.created_at, "user_name": inv.user_name, "thread_id": inv.thread_id,
            "question": inv.question, "status": inv.status, "report": inv.report}


@router.get("/{inv_id}/report.md", response_class=PlainTextResponse)
def download_report(inv_id: str, principal: Annotated[Principal, Depends(require_role(Role.analyst))]):
    inv = _get(inv_id, principal)
    if not inv.report:
        raise HTTPException(409, "Investigation has no report")
    md = report_to_markdown(inv.report, created_at=inv.created_at.isoformat(), user=inv.user_name)
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="incident-{inv.id}.md"'})
