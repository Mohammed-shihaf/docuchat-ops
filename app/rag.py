"""Retrieval-Augmented Generation: chunk -> embed -> store -> retrieve -> prompt -> answer."""
import uuid

from . import metrics as m
from .config import settings

SYSTEM_PROMPT = (
    "You answer questions using ONLY the context provided. "
    "If the answer is not in the context, say you don't know. "
    "After each fact, cite the source file name in brackets, e.g. [aks-basics.md]. "
    "If the user asks for a diagram, flowchart or visual, also include ONE diagram as a fenced "
    "```mermaid code block using flowchart TD, built only from facts in the context. "
    "Use short plain node IDs with quoted labels, exactly like: A[\"Developer\"] --> B[\"GitHub\"]. "
    "Never quote node IDs and do not use parentheses inside labels."
)


def chunk_text(text: str, size: int = settings.chunk_size, overlap: int = settings.chunk_overlap) -> list[str]:
    """Split text into overlapping character windows so facts on a boundary are not lost."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = " ".join(text.split())
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


def build_messages(question: str, hits: list[dict]) -> list[dict]:
    context = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


class RagService:
    """Wraps the vector store, embedder and LLM. Every call names its collection (one per session)."""

    def __init__(self, qdrant=None, embedder=None, llm=None):
        if qdrant is None:
            from qdrant_client import QdrantClient
            qdrant = QdrantClient(url=settings.qdrant_url)
        if embedder is None:
            from fastembed import TextEmbedding
            embedder = TextEmbedding(model_name=settings.embed_model, cache_dir=settings.embed_cache_dir)
        if llm is None:
            from openai import OpenAI
            llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "missing")
        self.qdrant, self.embedder, self.llm = qdrant, embedder, llm

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.embedder.embed(texts)]

    def create_collection(self, collection: str) -> None:
        self._ensure_collection(collection, len(self._embed(["dimension probe"])[0]))

    def _ensure_collection(self, collection: str, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams
        if not self.qdrant.collection_exists(collection):
            self.qdrant.create_collection(collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def ingest(self, source: str, text: str, collection: str = settings.collection) -> int:
        from qdrant_client.models import PointStruct
        chunks = chunk_text(text)
        if not chunks:
            return 0
        vectors = self._embed(chunks)
        self._ensure_collection(collection, len(vectors[0]))
        points = [
            # Deterministic IDs: re-uploading the same file overwrites instead of duplicating.
            PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{i}")), vector=v,
                        payload={"source": source, "text": c})
            for i, (c, v) in enumerate(zip(chunks, vectors))
        ]
        self.qdrant.upsert(collection, points=points)
        return len(points)

    def documents(self, collection: str = settings.collection) -> dict[str, int]:
        """Return {source file: chunk count} for everything in the collection."""
        if not self.qdrant.collection_exists(collection):
            return {}
        counts: dict[str, int] = {}
        offset = None
        while True:
            points, offset = self.qdrant.scroll(
                collection, limit=256, offset=offset, with_payload=["source"], with_vectors=False
            )
            for p in points:
                counts[p.payload["source"]] = counts.get(p.payload["source"], 0) + 1
            if offset is None:
                return dict(sorted(counts.items()))

    def drop(self, collection: str) -> None:
        if self.qdrant.collection_exists(collection):
            self.qdrant.delete_collection(collection)

    def retrieve(self, question: str, collection: str = settings.collection) -> list[dict]:
        if not self.qdrant.collection_exists(collection):
            return []
        vector = self._embed([question])[0]
        result = self.qdrant.query_points(collection, query=vector, limit=settings.top_k)
        return [{"source": p.payload["source"], "text": p.payload["text"], "score": p.score} for p in result.points]

    def answer(self, question: str, collection: str = settings.collection) -> dict:
        hits = self.retrieve(question, collection)
        if not hits:
            m.ANSWERS.labels("no_docs").inc()
            return {"answer": "I don't know - no documents have been added yet.", "sources": [], "citations": []}
        m.RETRIEVAL_SCORE.observe(max(h["score"] for h in hits))
        with m.LLM_LATENCY.time():
            resp = self.llm.chat.completions.create(
                model=settings.llm_model, messages=build_messages(question, hits), temperature=0.1
            )
        if usage := getattr(resp, "usage", None):
            m.LLM_TOKENS.labels("prompt").inc(usage.prompt_tokens or 0)
            m.LLM_TOKENS.labels("completion").inc(usage.completion_tokens or 0)
        answer = resp.choices[0].message.content or ""
        m.ANSWERS.labels("dont_know" if answer.lower().startswith("i don't know") else "yes").inc()
        return {
            "answer": answer,
            "sources": sorted({h["source"] for h in hits}),
            # The exact passages the answer was grounded on, so users can verify it.
            "citations": [{"source": h["source"], "text": h["text"], "score": round(h["score"], 3)} for h in hits],
        }
