import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import sessions
from .config import settings
from .parsers import UnsupportedFile, extract_text
from .rag import RagService

# Privacy: log events and counts only - never document text, file contents or questions.
log = logging.getLogger("docuchat")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# HTTP client logs include URLs with session IDs (the key to a user's data) - keep them quiet.
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


@lru_cache
def get_rag() -> RagService:
    return RagService()


async def cleanup_loop(rag: RagService) -> None:
    while True:
        with suppress(Exception):  # a temporary Qdrant outage must not kill the cleaner
            if n := await asyncio.to_thread(sessions.purge_expired, rag.qdrant):
                log.info("purged %d expired session(s)", n)
        await asyncio.sleep(settings.cleanup_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = None
    # Load the embedding model at startup so the first user question isn't slow.
    if os.getenv("WARMUP", "true") == "true":
        task = asyncio.create_task(cleanup_loop(get_rag()))
    yield
    if task:
        task.cancel()


app = FastAPI(title="DocuChat Ops", version="0.2.0", lifespan=lifespan)

Rag = Annotated[RagService, Depends(get_rag)]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def live_collection(rag: RagService, session_id: str) -> tuple[str, int]:
    found = sessions.find_collection(rag.qdrant, session_id)
    if not found:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return found


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(INDEX_HTML)


@app.get("/healthz")
def healthz():
    """Liveness probe: the process is up. Kept dependency-free on purpose."""
    return {"status": "ok"}


@app.post("/sessions", status_code=201)
def create_session(rag: Rag):
    """Start a private, temporary workspace. Everything in it is deleted when it expires."""
    session_id, expires_at = sessions.new_session()
    rag.create_collection(sessions.collection_name(session_id, expires_at))
    log.info("session created")
    return {"session_id": session_id, "expires_at": expires_at, "ttl_seconds": settings.session_ttl_seconds}


@app.get("/sessions/{session_id}")
def get_session(session_id: str, rag: Rag):
    collection, expires_at = live_collection(rag, session_id)
    docs = rag.documents(collection)
    return {"session_id": session_id, "expires_at": expires_at,
            "documents": [{"source": s, "chunks": n} for s, n in docs.items()]}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, rag: Rag):
    """'Delete my data now' - removes every chunk and vector for this session immediately."""
    found = sessions.find_collection(rag.qdrant, session_id)
    if found:
        rag.drop(found[0])
        log.info("session deleted by user")


@app.post("/sessions/{session_id}/files")
async def upload_file(session_id: str, request: Request, rag: Rag,
                      name: Annotated[str, Query(min_length=1, max_length=200)]):
    """Upload one file as the raw request body. It is parsed in memory and never written to disk."""
    collection, _ = live_collection(rag, session_id)
    if int(request.headers.get("content-length") or 0) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"file larger than {settings.max_file_bytes // 1048576} MB")
    data = await request.body()
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"file larger than {settings.max_file_bytes // 1048576} MB")
    docs = rag.documents(collection)
    if name not in docs and len(docs) >= settings.max_files_per_session:
        raise HTTPException(status_code=409, detail=f"limit of {settings.max_files_per_session} files per session")
    try:
        text = await asyncio.to_thread(extract_text, name, data)
    except UnsupportedFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="could not read this file (is it corrupted or password-protected?)") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="no text found (scanned PDFs need OCR, which is not supported yet)")
    chunks = await asyncio.to_thread(rag.ingest, name, text, collection)
    log.info("file ingested chunks=%d bytes=%d", chunks, len(data))
    return {"source": name, "chunks": chunks}


@app.post("/sessions/{session_id}/chat")
def chat(session_id: str, req: ChatRequest, rag: Rag):
    collection, _ = live_collection(rag, session_id)
    try:
        return rag.answer(req.question, collection)
    except Exception as exc:  # surface upstream LLM/vector-store failures as 502, not 500
        log.warning("chat upstream error type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}") from exc
