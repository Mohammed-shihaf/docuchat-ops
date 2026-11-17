"""Ephemeral sessions: each visitor gets a private collection that is deleted automatically.

Collection name:  s_<session_id>_<expires_epoch>_<owner>
  * session_id - random 128-bit token (the key the browser holds)
  * expires    - cleanup keeps working after a restart without any session table
  * owner      - keyed hash of the account that created it, or 'anon' (see auth.owner_tag)

A request must present BOTH the session ID and the matching owner (from the sign-in cookie), so a leaked or
guessed session ID is useless to anyone else - including other signed-in users.
"""
import re
import secrets
import time

from .config import settings

PREFIX = "s_"
NAME_RE = re.compile(r"^s_([a-f0-9]{32})_(\d+)(?:_([a-f0-9]{12}|anon))?$")


def new_session(now: float | None = None, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Return (session_id, expires_at). The id is a random 128-bit token."""
    now = time.time() if now is None else now
    return secrets.token_hex(16), int(now) + (ttl_seconds or settings.session_ttl_seconds)


def collection_name(session_id: str, expires_at: int, owner: str = "anon") -> str:
    return f"{PREFIX}{session_id}_{expires_at}_{owner}"


def find_collection(qdrant, session_id: str, owner: str = "anon", now: float | None = None) -> tuple[str, int] | None:
    """Look up a live session owned by `owner`. Expired, unknown or someone else's sessions return None,
    so callers can't distinguish 'not yours' from 'doesn't exist' (no ownership oracle)."""
    now = time.time() if now is None else now
    for c in qdrant.get_collections().collections:
        m = NAME_RE.match(c.name)
        if m and m.group(1) == session_id:
            expires_at = int(m.group(2))
            if expires_at <= now or (m.group(3) or "anon") != owner:
                return None
            return c.name, expires_at
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
