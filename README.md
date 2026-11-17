# DocuChat Ops

**Private, zero-retention Q&A over your documents, with a production-grade platform behind it.**
Upload PDFs (including scanned ones), Word files, images or notes. Ask questions and get answers grounded only in your files, with the exact passages shown and diagrams on request. Nothing is kept: every session deletes itself.

![CI](https://github.com/Mohammed-shihaf/docuchat-ops/actions/workflows/ci.yml/badge.svg)

```
 Browser ──► FastAPI (DocuChat API) ──► LLM (Groq / Ollama / Azure OpenAI)
               │  sessions · parsing · OCR · rate limit · /metrics
               ▼
            Qdrant (RAM only)        Prometheus ◄── /metrics ──► Grafana dashboard + alerts

 GitHub Actions: lint → tests → Helm/Terraform checks → build → Trivy scan → smoke test
                 → push to GHCR → answer-quality eval → bump image tag in gitops/
 Argo CD: watches gitops/ and syncs the Helm chart to the cluster (self-healing)
 Terraform: infra/local (kind + monitoring + Argo CD + app) · infra/azure (AKS + ACR + Key Vault)
```

## Privacy by design
| Guarantee | How it's enforced |
|---|---|
| Uploads never touch disk | Raw request body, parsed in memory. The API container is `read_only`; OCR scratch space is a RAM-only `/tmp`. |
| The vector store keeps nothing | Qdrant runs on tmpfs / `emptyDir: Memory`. A restart wipes everything. |
| Every session expires | Session collections are named `s_<random-id>_<expiry>`. A background job deletes them on time, even after restarts. |
| Delete immediately | The "Delete my data now" button calls `DELETE /sessions/{id}`. |
| Isolation | A 128-bit random session ID is the only key. A NetworkPolicy lets only the API reach Qdrant. |
| Logs and metrics are clean | No access logs and no file names, questions or session IDs. Metrics use route templates. The rate limiter keeps only salted hashes of IPs, in RAM. |
| No server-side chat history | Chat history lives only in the browser tab. |

> The LLM provider still processes the question and the retrieved passages. For the strictest setup, use a self-hosted model (`LLM_BASE_URL` pointing at Ollama/vLLM) or a provider with a zero-data-retention agreement.

## Features
- PDF, **scanned PDF and image OCR** (Tesseract), DOCX, Markdown and text. Limits: 10 MB per file, 20 files per session.
- Citations: source tags, plus a "Show the passages used" view with match scores.
- **Diagrams on request**: Mermaid flowcharts drawn from document facts.
- An auto-delete countdown, a responsive UI, and dark and light themes.
- A per-client rate limit (HTTP 429), and liveness and readiness probes.

## Run it

### Option 1: Docker Compose (fastest)
```bash
cp .env.example .env        # add a free Groq key (console.groq.com/keys) or point at Ollama
docker compose up -d --build
```
Open **http://localhost:8000**.

### Option 1b: Free public hosting, ₹0/month
Run it on an **Oracle Cloud Always Free** VM, with HTTPS via Caddy and a free DuckDNS domain. Use Groq's free tier, or Ollama on the VM for unlimited, fully private answers.
```bash
curl -fsSL https://raw.githubusercontent.com/Mohammed-shihaf/docuchat-ops/main/deploy/free-vm/setup.sh | bash
```
See [`deploy/free-vm`](deploy/free-vm) and **[COSTS.md](COSTS.md)** for the full ₹0 breakdown and the guardrails.

### Option 2: Full platform on local Kubernetes (Terraform)
Requires Docker, [kind](https://kind.sigs.k8s.io) and Terraform.
```bash
docker compose build api                              # builds docuchat-ops-api:latest
cd infra/local
export TF_VAR_llm_api_key=<your key>                  # PowerShell: $env:TF_VAR_llm_api_key="<key>"
terraform init && terraform apply
terraform output next_steps                           # port-forward commands for app, Grafana, Argo CD
```
Terraform writes its own `infra/local/.kubeconfig`, so your `~/.kube/config` is never modified.
Laptop-friendly by default (app only, about 2 GB). Add `-var enable_monitoring=true` for Prometheus and Grafana (about 1.5 GB more) and `-var enable_argocd=true` for Argo CD (about 1 GB more) when you have the RAM.
Verified on kind: pods run under a restricted security context, Qdrant storage is RAM-only, the eval scores **100%** through the cluster (p95 0.69 s), scanned-PDF OCR works and the rate limiter returns 429.

### Option 3: Azure (AKS), optional and paid
`infra/azure` creates a resource group, AKS (with workload identity, Azure Policy and network policy) and Key Vault (RBAC).
It is **cost-minimal by default**: free control plane, one B2ms node, ACR and Log Analytics off, budget alerts and `az aks stop` for idle time. CI never applies it.
```bash
cd infra/azure && terraform init && terraform plan -var subscription_id=<id>
```
Then install Argo CD and apply `gitops/application.yaml`. CI pushes images to GHCR and bumps `gitops/values-prod.yaml`, and Argo CD deploys them.
> Make the GHCR package public, or add an `imagePullSecret`, so the cluster can pull it.

## CI/CD (`.github/workflows/ci.yml`)
| Job | What it does |
|---|---|
| test | ruff + 25 pytest tests (fakes, no network) |
| iac | `helm lint/template`, `terraform fmt -check` and `validate` for both stacks |
| image | Build → **Trivy** (fail on fixable CRITICAL) → smoke test → push `sha-xxxxxxx` + `latest` to GHCR |
| eval | Starts the stack and runs `evals/run_eval.py`. **Fails below 80%** (needs the `LLM_API_KEY` repo secret) |
| deploy | Commits the new image tag to `gitops/values-prod.yaml`, then Argo CD syncs it |

## LLMOps: evals
`evals/run_eval.py` uploads a test handbook, asks 10 questions and scores:
- **accuracy**: the expected fact is present and the right source is cited
- **groundedness**: off-topic questions must get "I don't know" (no hallucination)
- **latency** (p95)

Current result: **100% accuracy, 100% groundedness, p95 0.84 s** (Groq `openai/gpt-oss-20b`).

## Observability
`/metrics` (Prometheus) exposes request rate and latency by route, LLM latency and tokens, answer outcomes (grounded / don't-know), retrieval score, files by type and result, active sessions and rate-limit hits.
The chart ships a **Grafana dashboard** (14 panels, auto-loaded) and **alerts**: error rate > 5%, p95 chat > 10 s, LLM errors, and session cleanup stalled.

## Helm chart (`charts/docuchat`)
- API Deployment with HPA (2–6 replicas) and a PDB.
- Qdrant on RAM volumes, plus a NetworkPolicy.
- A restricted security context everywhere: non-root, read-only root filesystem, all capabilities dropped, seccomp.
- Startup, liveness and readiness probes.
- Optional Ingress, ServiceMonitor, PrometheusRule and dashboard ConfigMap.
- Secrets come from an existing Secret, never from values.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | Start a private session |
| GET | `/sessions/{id}` | Session info and documents |
| POST | `/sessions/{id}/files?name=<file>` | Upload one file (raw body) |
| POST | `/sessions/{id}/chat` | Ask; returns `answer`, `sources` and `citations` |
| DELETE | `/sessions/{id}` | Delete all data now |
| GET | `/healthz`, `/readyz`, `/metrics` | Probes and metrics |

## Develop
```bash
py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q && .venv/Scripts/python -m ruff check .
```
Settings (env): `SESSION_TTL_SECONDS` 7200 · `MAX_FILE_MB` 10 · `MAX_FILES_PER_SESSION` 20 · `RATE_LIMIT_PER_MINUTE` 30 · `TRUST_PROXY_HEADERS` · `TOP_K` 4 · `CHUNK_SIZE` 800 · `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`
