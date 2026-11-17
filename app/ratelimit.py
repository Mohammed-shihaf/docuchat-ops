"""In-memory sliding-window rate limiter.

Privacy: client IPs are never stored - only a salted hash that rotates on every restart,
kept in RAM for the length of one window.
"""
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

_SALT = secrets.token_bytes(16)


def client_key(ip: str) -> str:
    return hashlib.blake2b(ip.encode(), key=_SALT, digest_size=8).hexdigest()


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory: drop idle keys
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True
