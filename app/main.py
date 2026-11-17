import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import auth, billing, sessions
from . import metrics as m
from .config import settings
from .db import Store
from .parsers import UnsupportedFile, extract_text, ocr_available
from .plans import PLANS, effective_plan, public_plans
from .rag import RagService
from .ratelimit import RateLimiter, client_key

# Privacy: log events and counts only - never document text, file contents or questions.
log = logging.getLogger("docuchat")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# HTTP client logs include URLs with session IDs (the key to a user's data) - keep them quiet.
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


@lru_cache
def get_rag() -> RagService:
    return RagService()


@lru_cache
def get_store() -> Store:
    return Store(settings.database_path)


async def cleanup_loop(rag: RagService) -> None:
    while True:
        with suppress(Exception):  # a temporary Qdrant outage must not kill the cleaner
            if n := await asyncio.to_thread(sessions.purge_expired, rag.qdrant):
                m.SESSIONS_DELETED.labels("expired").inc(n)
                log.info("purged %d expired session(s)", n)
            m.ACTIVE_SESSIONS.set(await asyncio.to_thread(sessions.count_live, rag.qdrant))
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


app = FastAPI(title="DocuChat Ops", version="1.0.0", lifespan=lifespan)

write_limiter = RateLimiter(settings.rate_limit_per_minute)
UNMETERED = {"/healthz", "/readyz", "/metrics"}


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers and (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def observe(request: Request, call_next):
    path = request.url.path
    if request.method == "POST" and not write_limiter.allow(client_key(client_ip(request))):
        m.RATE_LIMITED.inc()
        return JSONResponse({"detail": "too many requests - please wait a minute"}, status_code=429,
                            headers={"Retry-After": "60"})
    start = time.perf_counter()
    response = await call_next(request)
    if path not in UNMETERED:
        # Use the route template (/sessions/{session_id}/chat), never the raw path with the session ID.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        m.LATENCY.labels(route).observe(time.perf_counter() - start)
        m.REQUESTS.labels(route, request.method, str(response.status_code)).inc()
    return response

Rag = Annotated[RagService, Depends(get_rag)]
Db = Annotated[Store, Depends(get_store)]


def current_user(store: Db, dc_auth: Annotated[str | None, Cookie()] = None) -> dict | None:
    return auth.user_for_cookie(store, dc_auth)


User = Annotated[dict | None, Depends(current_user)]


def require_user(user: User) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="please sign in")
    return user


SignedIn = Annotated[dict, Depends(require_user)]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class CheckoutRequest(BaseModel):
    plan: str


class VerifyRequest(BaseModel):
    razorpay_order_id: str = Field(max_length=64)
    razorpay_payment_id: str = Field(max_length=64)
    razorpay_signature: str = Field(max_length=128)


def owner_of(user: dict) -> str:
    return auth.owner_tag(user["id"])


def live_collection(rag: RagService, session_id: str, user: dict) -> tuple[str, int]:
    """The ownership check: a session is only reachable with its ID *and* its owner's sign-in."""
    found = sessions.find_collection(rag.qdrant, session_id, owner_of(user))
    if not found:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return found


def plan_of(user: dict) -> dict:
    return PLANS[effective_plan(user)]


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(INDEX_HTML)


@app.get("/healthz")
def healthz():
    """Liveness probe: the process is up. Kept dependency-free on purpose."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(rag: Rag):
    """Readiness probe: only receive traffic when the vector store is reachable."""
    try:
        rag.qdrant.get_collections()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="vector store unavailable") from exc
    return {"status": "ready", "ocr": ocr_available()}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/sessions", status_code=201)
def create_session(rag: Rag, user: SignedIn):
    """Start a private, temporary workspace bound to the caller. Everything in it is deleted when it expires."""
    session_id, expires_at = sessions.new_session(ttl_seconds=plan_of(user)["session_hours"] * 3600)
    rag.create_collection(sessions.collection_name(session_id, expires_at, owner_of(user)))
    m.SESSIONS_CREATED.inc()
    log.info("session created")
    return {"session_id": session_id, "expires_at": expires_at, "ttl_seconds": expires_at - int(time.time())}


@app.get("/sessions/{session_id}")
def get_session(session_id: str, rag: Rag, user: SignedIn):
    collection, expires_at = live_collection(rag, session_id, user)
    docs = rag.documents(collection)
    return {"session_id": session_id, "expires_at": expires_at,
            "documents": [{"source": s, "chunks": n} for s, n in docs.items()]}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, rag: Rag, user: SignedIn):
    """'Delete my data now' - removes every chunk and vector for this session immediately."""
    found = sessions.find_collection(rag.qdrant, session_id, owner_of(user))
    if found:
        rag.drop(found[0])
        m.SESSIONS_DELETED.labels("user").inc()
        log.info("session deleted by user")


@app.post("/sessions/{session_id}/files")
async def upload_file(session_id: str, request: Request, rag: Rag, store: Db, user: SignedIn,
                      name: Annotated[str, Query(min_length=1, max_length=200)]):
    """Upload one file as the raw request body. It is parsed in memory and never written to disk."""
    collection, _ = live_collection(rag, session_id, user)
    limit = min(plan_of(user)["files_per_session"], settings.max_files_per_session)
    if int(request.headers.get("content-length") or 0) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"file larger than {settings.max_file_bytes // 1048576} MB")
    data = await request.body()
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=f"file larger than {settings.max_file_bytes // 1048576} MB")
    docs = rag.documents(collection)
    if name not in docs and len(docs) >= limit:
        raise HTTPException(status_code=402, detail=f"your plan allows {limit} files per session - upgrade for more")
    ftype = Path(name).suffix.lower().lstrip(".")[:8] or "none"
    try:
        text = await asyncio.to_thread(extract_text, name, data)
    except UnsupportedFile as exc:
        m.FILES.labels("other", "unsupported").inc()
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:
        m.FILES.labels(ftype, "unreadable").inc()
        raise HTTPException(status_code=422, detail="could not read this file (is it corrupted or password-protected?)") from exc
    if not text.strip():
        m.FILES.labels(ftype, "empty").inc()
        raise HTTPException(status_code=422, detail="no readable text found in this file")
    chunks = await asyncio.to_thread(rag.ingest, name, text, collection)
    store.consume(user["id"], "files", None)  # counted for the usage page; files are limited per session
    m.FILES.labels(ftype, "ok").inc()
    m.CHUNKS.inc(chunks)
    log.info("file ingested type=%s chunks=%d bytes=%d", ftype, chunks, len(data))
    return {"source": name, "chunks": chunks}


@app.post("/sessions/{session_id}/chat")
def chat(session_id: str, req: ChatRequest, rag: Rag, store: Db, user: SignedIn):
    collection, _ = live_collection(rag, session_id, user)
    plan = plan_of(user)
    if not store.consume(user["id"], "questions", plan["questions_per_month"]):
        raise HTTPException(status_code=402, detail=f"you have used all {plan['questions_per_month']} questions "
                                                    f"in your {plan['name']} plan this month - upgrade for more")
    try:
        return rag.answer(req.question, collection, allow_diagrams=plan["diagrams"], show_passages=plan["passages"])
    except Exception as exc:  # surface upstream LLM/vector-store failures as 502, not 500
        m.LLM_ERRORS.labels(type(exc).__name__).inc()
        log.warning("chat upstream error type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}") from exc


# =====================================================================================
# Accounts
# =====================================================================================
def _set_auth_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(auth.COOKIE, token, max_age=settings.auth_session_days * 86400, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/")


@app.post("/auth/login")
def login(req: LoginRequest, store: Db):
    """Email a one-time sign-in link. Same response whether or not the email exists (no account enumeration)."""
    if not auth.valid_email(req.email):
        raise HTTPException(status_code=422, detail="please enter a valid email")
    link = auth.create_login_link(store, req.email)
    if settings.smtp_host:
        try:
            auth.send_login_email(req.email.strip(), link)
        except Exception as exc:
            log.warning("login email failed type=%s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="could not send the email - try again shortly") from exc
        return {"sent": True}
    if settings.dev_login:  # local development only: no SMTP configured, hand the link back directly
        return {"sent": False, "dev_link": link}
    raise HTTPException(status_code=503, detail="sign-in email is not configured on this server")


@app.get("/auth/verify", include_in_schema=False)
def verify(token: str, store: Db):
    email = auth.redeem_login_token(store, token)
    if not email:
        return RedirectResponse("/?login=expired", status_code=303)
    user = store.get_or_create_user(email)
    resp = RedirectResponse("/?login=ok", status_code=303)
    _set_auth_cookie(resp, auth.start_auth_session(store, user["id"]))
    log.info("user signed in")
    return resp


@app.post("/auth/logout", status_code=204)
def logout(store: Db, dc_auth: Annotated[str | None, Cookie()] = None):
    auth.end_auth_session(store, dc_auth)
    resp = Response(status_code=204)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/me")
def me(store: Db, user: User):
    """The signed-in user's own account, plan and usage - nobody else's."""
    if not user:
        return {"signed_in": False, "payments_enabled": billing.configured()}
    plan_id = effective_plan(user)
    plan = PLANS[plan_id]
    return {"signed_in": True, "email": user["email"], "plan": plan_id, "plan_name": plan["name"],
            "plan_expires_at": user["plan_expires_at"] if plan_id in ("pro", "team") else None,
            "limits": plan, "usage": store.usage(user["id"]), "payments_enabled": billing.configured()}


@app.delete("/me", status_code=204)
def delete_account(store: Db, user: SignedIn):
    """Erase the account: email, sign-ins, usage and payment records (Razorpay keeps its own legal copy)."""
    store.delete_user(user["id"])
    resp = Response(status_code=204)
    resp.delete_cookie(auth.COOKIE, path="/")
    log.info("account deleted by user")
    return resp


# =====================================================================================
# Billing (Razorpay)
# =====================================================================================
@app.get("/billing/plans")
def plans():
    return {"plans": public_plans(), "payments_enabled": billing.configured()}


@app.post("/billing/checkout")
def checkout(req: CheckoutRequest, store: Db, user: SignedIn):
    if not billing.configured():
        raise HTTPException(status_code=503, detail="payments are not enabled on this server yet")
    try:
        return billing.create_order(store, user, req.plan)
    except billing.BillingError as exc:
        raise HTTPException(status_code=400 if str(exc) == "unknown plan" else 502, detail=str(exc)) from exc


@app.post("/billing/verify")
def verify_payment(req: VerifyRequest, store: Db, user: SignedIn):
    """Called by Checkout's success handler. The signature proves Razorpay issued it; the order must be this user's."""
    if not billing.checkout_signature_ok(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="payment signature is invalid")
    pay = billing.mark_paid(store, req.razorpay_order_id, req.razorpay_payment_id, expected_user_id=user["id"])
    if not pay:
        raise HTTPException(status_code=404, detail="order not found")
    log.info("payment verified plan=%s", pay["plan"])
    return {"status": "paid", "plan": pay["plan"]}


@app.post("/billing/webhook", include_in_schema=False)
async def webhook(request: Request, store: Db):
    body = await request.body()
    if not billing.webhook_signature_ok(body, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(status_code=400, detail="bad signature")
    result = billing.handle_webhook(store, json.loads(body))
    log.info("razorpay webhook result=%s", result)
    return {"result": result}


@app.get("/billing/history")
def payment_history(store: Db, user: SignedIn):
    """Only the caller's own payments."""
    return {"payments": billing.history(store, user["id"])}
