from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .rag import RagService

app = FastAPI(title="DocuChat Ops", version="0.1.0")


@lru_cache
def get_rag() -> RagService:
    return RagService()


Rag = Annotated[RagService, Depends(get_rag)]


class IngestRequest(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/healthz")
def healthz():
    """Liveness probe: the process is up. Kept dependency-free on purpose."""
    return {"status": "ok"}


@app.post("/ingest")
def ingest(req: IngestRequest, rag: Rag):
    return {"source": req.source, "chunks": rag.ingest(req.source, req.text)}


@app.post("/chat")
def chat(req: ChatRequest, rag: Rag):
    try:
        return rag.answer(req.question)
    except Exception as exc:  # surface upstream LLM/vector-store failures as 502, not 500
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}") from exc
