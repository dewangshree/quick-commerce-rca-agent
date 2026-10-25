"""Knowledge-base retrieval (role: analyst+)."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from qc_rca.db.models import Role
from qc_rca.rag.store import corpus_stats, retrieve
from qc_rca.security.auth import Principal, require_role

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
DocType = Literal["metric_definition", "schema", "playbook", "sla", "sop", "incident", "reference"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(default=4, ge=1, le=10)
    doc_types: list[DocType] | None = None
    include_samples: bool = True


@router.post("/search")
def search(body: SearchRequest, _: Annotated[Principal, Depends(require_role(Role.analyst))]):
    return retrieve(body.query, k=body.k, doc_types=body.doc_types, include_samples=body.include_samples)


@router.get("/stats")
def stats(_: Annotated[Principal, Depends(require_role(Role.viewer))]):
    return corpus_stats()
