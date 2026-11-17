"""Retrieval-Augmented Generation: chunk -> embed -> store -> retrieve -> prompt -> answer."""
import uuid

from .config import settings

SYSTEM_PROMPT = (
    "You answer questions using ONLY the context provided. "
    "If the answer is not in the context, say you don't know. "
    "After each fact, cite the source file name in brackets, e.g. [aks-basics.md]."
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
    """Wraps the vector store, embedder and LLM. Dependencies are injectable for tests."""

    def __init__(self, qdrant=None, embedder=None, llm=None):
        if qdrant is None:
            from qdrant_client import QdrantClient
            qdrant = QdrantClient(url=settings.qdrant_url)
        if embedder is None:
            from fastembed import TextEmbedding
            embedder = TextEmbedding(model_name=settings.embed_model)
        if llm is None:
            from openai import OpenAI
            llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "missing")
        self.qdrant, self.embedder, self.llm = qdrant, embedder, llm

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.embedder.embed(texts)]

    def _ensure_collection(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams
        if not self.qdrant.collection_exists(settings.collection):
            self.qdrant.create_collection(
                settings.collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
            )

    def ingest(self, source: str, text: str) -> int:
        from qdrant_client.models import PointStruct
        chunks = chunk_text(text)
        if not chunks:
            return 0
        vectors = self._embed(chunks)
        self._ensure_collection(len(vectors[0]))
        points = [
            # Deterministic IDs: re-ingesting the same file overwrites instead of duplicating.
            PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{i}")), vector=v,
                        payload={"source": source, "text": c})
            for i, (c, v) in enumerate(zip(chunks, vectors))
        ]
        self.qdrant.upsert(settings.collection, points=points)
        return len(points)

    def retrieve(self, question: str) -> list[dict]:
        vector = self._embed([question])[0]
        result = self.qdrant.query_points(settings.collection, query=vector, limit=settings.top_k)
        return [{"source": p.payload["source"], "text": p.payload["text"], "score": p.score} for p in result.points]

    def answer(self, question: str) -> dict:
        hits = self.retrieve(question)
        if not hits:
            return {"answer": "I don't know - no documents have been ingested yet.", "sources": []}
        resp = self.llm.chat.completions.create(
            model=settings.llm_model, messages=build_messages(question, hits), temperature=0.1
        )
        return {
            "answer": resp.choices[0].message.content,
            "sources": sorted({h["source"] for h in hits}),
        }
