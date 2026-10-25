"""Backwards-compatible ``POST /chat`` used by the original Streamlit UI."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from qc_rca.api.routes.investigations import execute_and_store
from qc_rca.db.models import Role
from qc_rca.security.auth import Principal, rate_limited

router = APIRouter(tags=["legacy"])


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(max_length=40)


class ChatResponse(BaseModel):
    reply: str
    investigation_id: str


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, principal: Annotated[Principal, Depends(rate_limited(Role.analyst))]):
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(400, "messages must end with a user message")
    history = [m.model_dump() for m in req.messages[:-1]]
    report = await run_in_threadpool(execute_and_store, req.messages[-1].content, principal.name, None, history)
    reply = report["answer_markdown"]
    if report.get("narrative"):
        reply = f"{report['narrative']}\n\n{reply}"
    return ChatResponse(reply=reply, investigation_id=report["investigation_id"])
