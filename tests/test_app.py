"""Unit tests run with fakes: no Qdrant, model download or API key needed (fast, CI-friendly)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_rag
from app.rag import RagService, build_messages, chunk_text


class FakeEmbedder:
    def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


class FakeQdrant:
    def __init__(self):
        self.points = []

    def collection_exists(self, name):
        return bool(self.points)

    def create_collection(self, name, vectors_config):
        pass

    def upsert(self, name, points):
        ids = {p.id for p in points}
        self.points = [p for p in self.points if p.id not in ids] + points

    def query_points(self, name, query, limit):
        hits = [SimpleNamespace(payload=p.payload, score=0.9) for p in self.points[:limit]]
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
def client(rag):
    app.dependency_overrides[get_rag] = lambda: rag
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chunk_text_overlaps():
    chunks = chunk_text("a" * 1000, size=400, overlap=100)
    assert [len(c) for c in chunks] == [400, 400, 400, 100]


def test_chunk_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("abc", size=10, overlap=10)


def test_build_messages_includes_sources():
    msgs = build_messages("What is AKS?", [{"source": "aks.md", "text": "AKS is managed Kubernetes."}])
    assert msgs[0]["role"] == "system"
    assert "[aks.md]" in msgs[1]["content"] and "What is AKS?" in msgs[1]["content"]


def test_reingest_does_not_duplicate(rag):
    rag.ingest("a.md", "hello world")
    rag.ingest("a.md", "hello world")
    assert len(rag.qdrant.points) == 1


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_without_docs_says_dont_know(client):
    r = client.post("/chat", json={"question": "What is AKS?"})
    assert r.status_code == 200 and r.json()["sources"] == []


def test_ingest_then_chat(client, rag):
    assert client.post("/ingest", json={"source": "aks.md", "text": "AKS is managed Kubernetes."}).json()["chunks"] == 1
    r = client.post("/chat", json={"question": "What is AKS?"}).json()
    assert r["sources"] == ["aks.md"] and "AKS" in r["answer"]
    assert "AKS is managed Kubernetes." in rag.llm.last_messages[1]["content"]


def test_chat_validation(client):
    assert client.post("/chat", json={"question": ""}).status_code == 422
