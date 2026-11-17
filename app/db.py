"""Account & billing store (SQLite). Every table is keyed by user_id, so one user's records can
never be read through another user's queries.

What lives here:   email, plan, usage counters, payment records, login/auth token *hashes*.
What never does:   documents, extracted text, questions, answers, file names.
"""
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id               TEXT PRIMARY KEY,
  email            TEXT NOT NULL UNIQUE,
  plan             TEXT NOT NULL DEFAULT 'free',
  plan_expires_at  INTEGER,                 -- paid plans: access until this time
  created_at       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_tokens (     -- one-time email sign-in links (hash only)
  token_hash  TEXT PRIMARY KEY,
  email       TEXT NOT NULL,
  expires_at  INTEGER NOT NULL,
  used        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_sessions (    -- browser sign-ins (hash only; cookie holds the token)
  token_hash  TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS usage (            -- counters only: never what was asked or uploaded
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period     TEXT NOT NULL,                   -- 'YYYY-MM'
  questions  INTEGER NOT NULL DEFAULT 0,
  files      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, period)
);
CREATE TABLE IF NOT EXISTS payments (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan                 TEXT NOT NULL,
  amount_paise         INTEGER NOT NULL,
  currency             TEXT NOT NULL,
  razorpay_order_id    TEXT NOT NULL UNIQUE,
  razorpay_payment_id  TEXT UNIQUE,
  status               TEXT NOT NULL,         -- created | paid | failed
  created_at           INTEGER NOT NULL,
  paid_at              INTEGER
);
CREATE INDEX IF NOT EXISTS ix_payments_user ON payments(user_id);
CREATE TABLE IF NOT EXISTS webhook_events (   -- idempotency: each Razorpay event is applied once
  event_id     TEXT PRIMARY KEY,
  received_at  INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        """Serialised transaction - SQLite is single-writer and this keeps check-then-write atomic."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, *args):
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, *args) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ---- users ----
    def get_or_create_user(self, email: str) -> dict:
        email = email.strip().lower()
        with self.tx() as c:
            row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not row:
                c.execute("INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)",
                          (uuid.uuid4().hex, email, int(time.time())))
                row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)

    def user(self, user_id: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE id = ?", user_id)

    def delete_user(self, user_id: str) -> None:
        """Right to erasure: cascades to sign-ins, usage and payment records."""
        with self.tx() as c:
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ---- usage (per user, per month) ----
    @staticmethod
    def period(now: float | None = None) -> str:
        return time.strftime("%Y-%m", time.gmtime(now or time.time()))

    def usage(self, user_id: str, period: str | None = None) -> dict:
        row = self.one("SELECT questions, files FROM usage WHERE user_id = ? AND period = ?",
                       user_id, period or self.period())
        return row or {"questions": 0, "files": 0}

    def consume(self, user_id: str, kind: str, limit: int | None) -> bool:
        """Atomically add 1 to this user's counter if it is under the limit. Returns False when over."""
        assert kind in ("questions", "files")
        p = self.period()
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO usage (user_id, period) VALUES (?, ?)", (user_id, p))
            used = c.execute(f"SELECT {kind} FROM usage WHERE user_id = ? AND period = ?", (user_id, p)).fetchone()[0]
            if limit is not None and used >= limit:
                return False
            c.execute(f"UPDATE usage SET {kind} = {kind} + 1 WHERE user_id = ? AND period = ?", (user_id, p))
            return True
