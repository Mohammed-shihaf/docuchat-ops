# DocuChat Ops

**Private, zero-retention Q&A over your documents.** Upload PDFs, Word files or notes, ask questions, and get answers grounded in your files, with the exact passages shown and diagrams on request. Nothing is kept: every session deletes itself.

## Privacy by design
| Guarantee | How it's enforced |
|---|---|
| Uploads are never written to disk | Files arrive as the raw request body and are parsed in memory (`app/parsers.py`). The API container is `read_only`. |
| The vector store keeps nothing | Qdrant runs on **tmpfs** (RAM). A restart wipes everything. It has no host port, so only the API can reach it. |
| Every session expires | Each session is a private collection named `s_<random-id>_<expiry>`. A background job deletes expired ones every minute, and this keeps working across restarts. |
| Users can delete immediately | "Delete my data now" calls `DELETE /sessions/{id}`. |
| Sessions are isolated | A 128-bit random session ID is the only key to a session's data. |
| Logs contain no customer data | Only counts and event names are logged. There is no access log (URLs carry file names), and HTTP client logs (which carry session IDs) are silenced. |
| Chat history is never stored server-side | It lives only in the browser tab. |

> The LLM provider still processes the question and the retrieved passages. For strict use, point `LLM_BASE_URL` at a self-hosted model (Ollama/vLLM) or a provider with a zero-data-retention agreement.

## Features
- PDF, DOCX, Markdown and text upload (10 MB per file, 20 files per session)
- Answers cite source files, and "Show the passages used" reveals the exact retrieved text
- **Diagrams**: ask "explain … with a diagram" and a Mermaid flowchart is drawn from the document's facts
- A live countdown to auto-deletion
- A responsive UI that works on phone and desktop, in dark or light theme

## Run it
```bash
cp .env.example .env                 # add your free Groq key, or point to Ollama
docker compose up -d --build
```
Open **http://localhost:8000**. API docs are at http://localhost:8000/docs. Sample files to try are in `docs/`.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | Start a private session; returns `session_id` and `expires_at` |
| GET | `/sessions/{id}` | Session info and its documents |
| POST | `/sessions/{id}/files?name=<file>` | Upload one file (raw body) |
| POST | `/sessions/{id}/chat` | Ask a question; returns `answer`, `sources` and `citations` |
| DELETE | `/sessions/{id}` | Delete all data now |

## Develop
```bash
py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q      # 19 tests with fakes, no network needed
.venv/Scripts/python -m ruff check .
```

## Settings (env)
`SESSION_TTL_SECONDS` (default 7200) · `MAX_FILE_MB` (10) · `MAX_FILES_PER_SESSION` (20) · `TOP_K` (4) · `CHUNK_SIZE` (800) · `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`

## Roadmap
Kubernetes + Helm + Terraform (Week 2) · CI/CD + GitOps (Week 3) · Prometheus/Grafana + Langfuse evals (Week 4) · OCR for scanned PDFs · rate limiting · self-hosted package
