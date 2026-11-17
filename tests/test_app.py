"""Unit tests run with fakes: no Qdrant, model download or API key needed (fast, CI-friendly)."""
import io
import os
from types import SimpleNamespace

os.environ["WARMUP"] = "false"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["DATABASE_PATH"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from app import sessions
from app.db import Store
from app.main import app, get_rag, get_store
from app.parsers import UnsupportedFile, extract_text
from app.rag import RagService, build_messages, chunk_text


class FakeEmbedder:
    def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


class FakeQdrant:
    """Multi-collection in-memory stand-in for QdrantClient."""

    def __init__(self):
        self.cols: dict[str, list] = {}

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.cols])

    def collection_exists(self, name):
        return name in self.cols

    def create_collection(self, name, vectors_config):
        self.cols.setdefault(name, [])

    def delete_collection(self, name):
        self.cols.pop(name, None)

    def upsert(self, name, points):
        ids = {p.id for p in points}
        self.cols[name] = [p for p in self.cols[name] if p.id not in ids] + points

    def scroll(self, name, limit, offset, with_payload, with_vectors):
        return [SimpleNamespace(payload=p.payload) for p in self.cols[name]], None

    def query_points(self, name, query, limit):
        hits = [SimpleNamespace(payload=p.payload, score=0.9) for p in self.cols[name][:limit]]
        return SimpleNamespace(points=hits)


class FakeLLM:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.last_messages = None

    def _create(self, model, messages, temperature):
        self.last_messages = messages
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="AKS is managed Kubernetes [aks.md]"))])


@pytest.fixture
def rag():
    return RagService(qdrant=FakeQdrant(), embedder=FakeEmbedder(), llm=FakeLLM())


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def client(rag, store, monkeypatch):
    import app.main as main_mod
    from app.ratelimit import RateLimiter
    monkeypatch.setattr(main_mod, "write_limiter", RateLimiter(10_000))  # isolate tests from each other
    app.dependency_overrides[get_rag] = lambda: rag
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sid(client):
    r = client.post("/sessions")
    assert r.status_code == 201
    return r.json()["session_id"]


def upload(client, sid, name, data: bytes):
    return client.post(f"/sessions/{sid}/files", params={"name": name}, content=data,
                       headers={"Content-Type": "application/octet-stream"})


# ---- pure functions ----

def test_chunk_text_overlaps():
    assert [len(c) for c in chunk_text("a" * 1000, size=400, overlap=100)] == [400, 400, 400, 100]


def test_chunk_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("abc", size=10, overlap=10)


def test_build_messages_includes_sources_and_diagram_rule():
    msgs = build_messages("What is AKS?", [{"source": "aks.md", "text": "AKS is managed Kubernetes."}])
    assert "mermaid" in msgs[0]["content"]
    assert "[aks.md]" in msgs[1]["content"] and "What is AKS?" in msgs[1]["content"]


def test_extract_text_txt_and_unsupported():
    assert extract_text("a.txt", b"hello") == "hello"
    with pytest.raises(UnsupportedFile):
        extract_text("a.exe", b"x")


def test_extract_text_docx():
    from docx import Document
    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("Node pools group VMs.")
    d.save(buf)
    assert "Node pools group VMs." in extract_text("a.docx", buf.getvalue())


def test_extract_text_pdf():
    from pypdf import PdfWriter
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    assert extract_text("a.pdf", buf.getvalue()) == ""  # blank page -> no text, but parses


# ---- sessions & expiry ----

def test_purge_deletes_only_expired():
    q = FakeQdrant()
    live = sessions.collection_name("a" * 32, 2000)
    dead = sessions.collection_name("b" * 32, 1000)
    q.cols = {live: [], dead: [], "docs": []}
    assert sessions.purge_expired(q, now=1500) == 1
    assert set(q.cols) == {live, "docs"}


def test_find_collection_ignores_expired():
    q = FakeQdrant()
    q.cols = {sessions.collection_name("c" * 32, 1000): []}
    assert sessions.find_collection(q, "c" * 32, now=999) is not None
    assert sessions.find_collection(q, "c" * 32, now=1001) is None


def test_sessions_are_isolated(client):
    a, b = client.post("/sessions").json()["session_id"], client.post("/sessions").json()["session_id"]
    upload(client, a, "secret.md", b"only for A")
    assert client.get(f"/sessions/{a}").json()["documents"] == [{"source": "secret.md", "chunks": 1}]
    assert client.get(f"/sessions/{b}").json()["documents"] == []


def test_delete_session_removes_data(client, sid, rag):
    upload(client, sid, "a.md", b"hello")
    assert client.delete(f"/sessions/{sid}").status_code == 204
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert not any(sid in name for name in rag.qdrant.cols)


def test_unknown_session_404(client):
    assert client.post("/sessions/" + "f" * 32 + "/chat", json={"question": "hi"}).status_code == 404


# ---- API ----

def test_ui_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "DocuChat Ops" in r.text


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_without_docs_says_dont_know(client, sid):
    r = client.post(f"/sessions/{sid}/chat", json={"question": "What is AKS?"})
    assert r.status_code == 200 and r.json()["sources"] == []


def test_upload_then_chat_with_citations(client, sid, rag):
    assert upload(client, sid, "aks.md", b"AKS is managed Kubernetes.").json()["chunks"] == 1
    r = client.post(f"/sessions/{sid}/chat", json={"question": "What is AKS?"}).json()
    assert r["sources"] == ["aks.md"] and "AKS" in r["answer"]
    assert r["citations"][0]["text"] == "AKS is managed Kubernetes."
    assert "AKS is managed Kubernetes." in rag.llm.last_messages[1]["content"]


def test_reupload_does_not_duplicate(client, sid):
    upload(client, sid, "a.md", b"hello world")
    upload(client, sid, "a.md", b"hello world")
    assert client.get(f"/sessions/{sid}").json()["documents"] == [{"source": "a.md", "chunks": 1}]


def test_upload_rejects_unsupported_and_empty(client, sid):
    assert upload(client, sid, "virus.exe", b"MZ").status_code == 415
    assert upload(client, sid, "empty.txt", b"   ").status_code == 422


def test_upload_size_limit(client, sid, monkeypatch):
    import dataclasses

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "settings", dataclasses.replace(main_mod.settings, max_file_bytes=10))
    assert upload(client, sid, "big.txt", b"x" * 11).status_code == 413


def test_chat_validation(client, sid):
    assert client.post(f"/sessions/{sid}/chat", json={"question": ""}).status_code == 422


# ---- rate limiting, metrics, probes ----

def test_rate_limiter_window():
    from app.ratelimit import RateLimiter
    rl = RateLimiter(limit=2, window_seconds=60)
    assert rl.allow("k", now=0) and rl.allow("k", now=1)
    assert not rl.allow("k", now=2)
    assert rl.allow("k", now=61)          # oldest hit expired
    assert rl.allow("other", now=2)       # keys are independent


def test_client_key_does_not_store_raw_ip():
    from app.ratelimit import client_key
    assert "203.0.113.9" not in client_key("203.0.113.9")
    assert client_key("203.0.113.9") == client_key("203.0.113.9")


def test_api_returns_429_when_limited(client, monkeypatch):
    import app.main as main_mod
    from app.ratelimit import RateLimiter
    monkeypatch.setattr(main_mod, "write_limiter", RateLimiter(limit=1))
    assert client.post("/sessions").status_code == 201
    r = client.post("/sessions")
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"


def test_metrics_use_route_templates_not_session_ids(client, sid):
    upload(client, sid, "a.md", b"hello")
    client.post(f"/sessions/{sid}/chat", json={"question": "hi"})
    body = client.get("/metrics").text
    assert 'route="/sessions/{session_id}/chat"' in body
    assert sid not in body and "a.md" not in body
    assert "docuchat_llm_tokens_total" in body


def test_readyz(client):
    assert client.get("/readyz").json()["status"] == "ready"


def test_image_without_ocr_is_rejected(monkeypatch):
    import app.parsers
    monkeypatch.setattr(app.parsers, "ocr_available", lambda: False)
    with pytest.raises(UnsupportedFile):
        extract_text("scan.png", b"\x89PNG")
