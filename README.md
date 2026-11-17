# DocuChat Ops

A RAG chatbot over public documentation. It is a small app wrapped in a production-style platform: Docker, Kubernetes, Terraform, GitOps, observability and LLMOps.

> Status: **Week 1**. The app runs locally with Docker Compose. Kubernetes, Terraform, CI/CD and observability are coming in Weeks 2–4.

## How it works
1. **Ingest:** documents are split into overlapping chunks and embedded locally with `fastembed` (BAAI/bge-small, CPU only, no API cost). The vectors are stored in **Qdrant**.
2. **Chat:** the question is embedded, the top-k chunks are retrieved, and an LLM answers using **only** that context, citing its sources.
3. **LLM:** any OpenAI-compatible endpoint works: Groq (free tier), Ollama (local) or Azure OpenAI. Set it in `.env`.

## Run it
```bash
cp .env.example .env                 # add your free Groq key, or point to Ollama
docker compose up -d --build
docker compose exec api python ingest.py docs/
curl -X POST localhost:8000/chat -H "Content-Type: application/json" -d '{"question":"Who pays for the AKS control plane?"}'
```
API docs: http://localhost:8000/docs

## Develop
```bash
py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q      # unit tests use fakes, no Qdrant or API key needed
.venv/Scripts/python -m ruff check .
```

## Design notes
- **Deterministic chunk IDs** (`uuid5(source#index)`), so re-ingesting a file overwrites its chunks instead of duplicating them.
- **Injectable dependencies** in `RagService`, so tests run in about 1 second with no network.
- **Non-root container, multi-stage build, health check.** These are ready for Kubernetes in Week 2.
- **Upstream failures return `502`,** not `500`, so alerts can tell "our bug" from "the LLM or vector DB is down".
