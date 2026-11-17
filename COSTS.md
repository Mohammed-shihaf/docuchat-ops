# Running DocuChat for ₹0

Every default in this repo is free. Paid options exist only behind explicit switches.

## The free stack
| Need | Free choice | Limits to know |
|---|---|---|
| Server | **Oracle Cloud Always Free**: Ampere A1, up to 4 vCPU / 24 GB RAM, 200 GB disk | Free permanently. ARM CPU, so the image is built multi-arch. Idle instances may be reclaimed, but real traffic keeps it active. |
| AI model | **Groq free tier** (`openai/gpt-oss-20b`) | Per-minute and per-day request caps. The app's rate limiter keeps usage under them. |
| AI model (unlimited) | **Ollama on the same VM** (`llama3.2:3b`) | Slower than Groq on CPU (a few seconds per answer), but no caps, no cost, and no data leaves the VM |
| Embeddings | `fastembed` bge-small, baked into the image, runs on CPU | No API at all |
| HTTPS | **Caddy** + Let's Encrypt | Automatic, renews itself |
| Domain | **DuckDNS** subdomain (`yourname.duckdns.org`) | Free. Buy a `.in` domain (~₹500/yr) only when you have customers. |
| CI/CD | **GitHub Actions** | Unlimited minutes for public repos |
| Image registry | **GHCR** | Free for public images |
| Deploys | Nightly `docker compose pull` cron on the VM | No CD server to run |
| Monitoring | `/metrics` + Prometheus/Grafana (Helm chart or Grafana Cloud free tier) | Grafana Cloud free: 10k series, 14-day retention |
| Local dev / demo | Docker Compose or kind on your own PC | ₹0 |

**Total: ₹0/month.**

## How costs are kept down in the code
- **No database or object storage to pay for.** Zero retention means sessions live in RAM and expire.
- **The embedding model runs locally.** No per-document embedding API cost.
- **Small LLM context:** only the top 4 chunks are sent, about 800 characters each, so token use per question stays low.
- **A rate limit** (30 writes per minute per client) stops one user from burning the free LLM quota.
- **Upload limits** (10 MB per file, 20 files per session) bound CPU and RAM per user.
- **One image runs everywhere:** laptop, free VM or Kubernetes.

## If you ever move to Azure (paid, optional)
`infra/azure` is **never applied by CI**. It's cheap by default:

| Setting | Default | Why |
|---|---|---|
| AKS control plane | Free tier (dev) | The control plane costs ₹0 |
| Nodes | **1 × B2ms** shared node, no extra pool (`enable_app_pool=false`) | The smallest size that fits the app |
| ACR | **off** (`enable_acr=false`), uses free GHCR | Saves about USD 5/month |
| Log Analytics | **off** (`enable_log_analytics=false`), uses in-cluster Prometheus | Avoids per-GB ingestion charges |
| Budget alert | Set `budget_alert_emails` to get emails at 50/80/100% | The budget resource itself is free |
| Idle | `terraform output stop_when_idle` gives `az aks stop` | A stopped cluster has ₹0 compute cost |

Rough cost with defaults: about USD 60/month for one B2ms node running 24×7 in Central India, or about ₹0 while stopped.
**Recommendation: don't use Azure until paying customers cover it.** The free VM handles early users comfortably.
