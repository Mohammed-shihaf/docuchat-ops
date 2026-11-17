"""Settings come from environment variables so the same image runs locally, in Compose and in Kubernetes."""
import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    collection: str = os.getenv("QDRANT_COLLECTION", "docs")
    embed_model: str = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    embed_cache_dir: str | None = os.getenv("EMBED_CACHE_DIR")  # set in the image, where the model is baked in
    # Any OpenAI-compatible endpoint: Groq (free tier), Ollama, Azure OpenAI...
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    top_k: int = int(os.getenv("TOP_K", "4"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "800"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    # Zero-retention limits
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", str(2 * 60 * 60)))
    cleanup_interval_seconds: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))
    max_file_bytes: int = int(os.getenv("MAX_FILE_MB", "10")) * 1024 * 1024
    max_files_per_session: int = int(os.getenv("MAX_FILES_PER_SESSION", "50"))
    # Abuse protection: POST requests (sessions, uploads, questions) per client per minute
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    # Only enable behind a trusted ingress that sets X-Forwarded-For
    trust_proxy_headers: bool = os.getenv("TRUST_PROXY_HEADERS", "false") == "true"

    # Accounts & billing. Only account/billing records live here - never documents or questions.
    database_path: str = os.getenv("DATABASE_PATH", "/data/docuchat.db")
    # Signs owner tags and login links. Set a long random value in production; the per-process
    # fallback means logins and session ownership reset on every restart.
    secret_key: str = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    public_url: str = os.getenv("PUBLIC_URL", "http://localhost:8000")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false") == "true"
    login_link_ttl_seconds: int = int(os.getenv("LOGIN_LINK_TTL_SECONDS", "900"))
    auth_session_days: int = int(os.getenv("AUTH_SESSION_DAYS", "30"))
    # Email delivery for sign-in links (e.g. Brevo/Gmail SMTP). Without it, DEV_LOGIN returns the link in the API response.
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "DocuChat <no-reply@localhost>")
    dev_login: bool = os.getenv("DEV_LOGIN", "false") == "true"
    # Razorpay (use rzp_test_ keys until KYC is done)
    razorpay_key_id: str = os.getenv("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    razorpay_webhook_secret: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")


settings = Settings()
