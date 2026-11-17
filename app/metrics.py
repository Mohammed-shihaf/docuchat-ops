"""Prometheus metrics. Labels are low-cardinality and never contain customer data."""
from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("docuchat_http_requests_total", "HTTP requests", ["route", "method", "status"])
LATENCY = Histogram("docuchat_http_request_seconds", "HTTP request latency", ["route"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 30))
SESSIONS_CREATED = Counter("docuchat_sessions_created_total", "Sessions created")
SESSIONS_DELETED = Counter("docuchat_sessions_deleted_total", "Sessions deleted", ["reason"])  # user|expired
ACTIVE_SESSIONS = Gauge("docuchat_active_sessions", "Live session collections")
FILES = Counter("docuchat_files_ingested_total", "Files ingested", ["type", "result"])
CHUNKS = Counter("docuchat_chunks_ingested_total", "Chunks embedded and stored")
LLM_LATENCY = Histogram("docuchat_llm_seconds", "LLM call latency",
                        buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 30))
LLM_TOKENS = Counter("docuchat_llm_tokens_total", "LLM tokens", ["kind"])  # prompt|completion
LLM_ERRORS = Counter("docuchat_llm_errors_total", "LLM/vector store failures", ["type"])
ANSWERS = Counter("docuchat_answers_total", "Answers", ["grounded"])  # yes|no_docs|dont_know
RETRIEVAL_SCORE = Histogram("docuchat_retrieval_top_score", "Best retrieval similarity per question",
                            buckets=(0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))
RATE_LIMITED = Counter("docuchat_rate_limited_total", "Requests rejected by the rate limiter")
