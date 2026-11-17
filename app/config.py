"""Settings come from environment variables so the same image runs locally, in Compose and in Kubernetes."""
import os
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
    max_files_per_session: int = int(os.getenv("MAX_FILES_PER_SESSION", "20"))


settings = Settings()
