"""Ephemeral sessions: each visitor gets a private collection that is deleted automatically.

The expiry time is encoded in the collection name (s_<id>_<expires_epoch>), so cleanup keeps
working after a restart without storing any session table anywhere.
"""
import re
import secrets
import time

from .config import settings

PREFIX = "s_"
NAME_RE = re.compile(r"^s_([a-f0-9]{32})_(\d+)$")


def new_session(now: float | None = None) -> tuple[str, int]:
    """Return (session_id, expires_at). The id is a random 128-bit token - it is the only key to the data."""
    now = time.time() if now is None else now
    return secrets.token_hex(16), int(now) + settings.session_ttl_seconds


def collection_name(session_id: str, expires_at: int) -> str:
    return f"{PREFIX}{session_id}_{expires_at}"


def find_collection(qdrant, session_id: str, now: float | None = None) -> tuple[str, int] | None:
    """Look up a live session's collection; expired or unknown sessions return None."""
    now = time.time() if now is None else now
    for c in qdrant.get_collections().collections:
        m = NAME_RE.match(c.name)
        if m and m.group(1) == session_id:
            expires_at = int(m.group(2))
            return (c.name, expires_at) if expires_at > now else None
    return None


def count_live(qdrant, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return sum(1 for c in qdrant.get_collections().collections
               if (m := NAME_RE.match(c.name)) and int(m.group(2)) > now)


def purge_expired(qdrant, now: float | None = None) -> int:
    """Delete every session collection past its expiry. Returns how many were deleted."""
    now = time.time() if now is None else now
    deleted = 0
    for c in qdrant.get_collections().collections:
        m = NAME_RE.match(c.name)
        if m and int(m.group(2)) <= now:
            qdrant.delete_collection(c.name)
            deleted += 1
    return deleted
