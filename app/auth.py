"""Passwordless email sign-in.

* A sign-in link carries a random token; only its SHA-256 hash is stored, it works once, and expires in 15 min.
* Signing in sets an HttpOnly cookie with a second random token; again only the hash is stored server-side,
  so a database leak cannot be replayed as a login.
* owner_tag(): a keyed hash of the user ID that is embedded in chat-session names, so a session is bound
  to the account that created it without putting any user ID or email into the vector store.
"""
import hashlib
import hmac
import re
import secrets
import smtplib
import time
from email.message import EmailMessage

from .config import settings
from .db import Store

COOKIE = "dc_auth"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def owner_tag(user_id: str | None) -> str:
    """12-hex keyed hash of the user ID, or 'anon'. Unforgeable without SECRET_KEY, reveals nothing."""
    if not user_id:
        return "anon"
    return hmac.new(settings.secret_key.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:12]


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email.strip()))


def create_login_link(store: Store, email: str) -> str:
    token = secrets.token_urlsafe(32)
    with store.tx() as c:
        c.execute("DELETE FROM login_tokens WHERE expires_at < ?", (int(time.time()),))
        c.execute("INSERT INTO login_tokens (token_hash, email, expires_at) VALUES (?, ?, ?)",
                  (_hash(token), email.strip().lower(), int(time.time()) + settings.login_link_ttl_seconds))
    return f"{settings.public_url}/auth/verify?token={token}"


def send_login_email(email: str, link: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your DocuChat sign-in link"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    msg.set_content(f"Sign in to DocuChat:\n\n{link}\n\nThis link works once and expires in "
                    f"{settings.login_link_ttl_seconds // 60} minutes. If you didn't ask for it, ignore this email.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def redeem_login_token(store: Store, token: str) -> str | None:
    """Single-use: returns the email and burns the token, or None if invalid/expired/used."""
    with store.tx() as c:
        row = c.execute("SELECT email, expires_at, used FROM login_tokens WHERE token_hash = ?", (_hash(token),)).fetchone()
        if not row or row["used"] or row["expires_at"] < time.time():
            return None
        c.execute("UPDATE login_tokens SET used = 1 WHERE token_hash = ?", (_hash(token),))
        return row["email"]


def start_auth_session(store: Store, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with store.tx() as c:
        c.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                  (_hash(token), user_id, int(time.time()) + settings.auth_session_days * 86400))
    return token


def user_for_cookie(store: Store, token: str | None) -> dict | None:
    if not token:
        return None
    row = store.one("SELECT u.* FROM auth_sessions a JOIN users u ON u.id = a.user_id "
                    "WHERE a.token_hash = ? AND a.expires_at > ?", _hash(token), int(time.time()))
    return row


def end_auth_session(store: Store, token: str | None) -> None:
    if token:
        with store.tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_hash(token),))
