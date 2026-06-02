from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn

from database import init_db
from agent import chat

app = FastAPI(title="QC RCA Agent API")


@app.on_event("startup")
def startup():
    try:
        init_db()
        print("[API] Database initialized.")
    except Exception as e:
        print(f"[API] WARNING: DB init failed: {e}")


class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")
    try:
        msgs = [{"role": m.role, "content": m.content} for m in req.messages]
        reply = chat(msgs)
        return ChatResponse(reply=reply)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
