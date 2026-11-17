"""Razorpay payments (Orders API + Checkout). Card/UPI details never touch our servers.

Flow:  POST /billing/checkout  -> we create a Razorpay order tied to the signed-in user (payments row: created)
       Razorpay Checkout       -> customer pays on Razorpay's own form
       POST /billing/verify    -> browser returns (order_id, payment_id, signature); we verify the HMAC
       POST /billing/webhook   -> Razorpay confirms server-to-server (backup path, idempotent)
Either confirmation marks the payment 'paid' and extends that same user's plan by 30 days.
"""
import hashlib
import hmac
import time
import uuid

import httpx

from .config import settings
from .db import Store
from .plans import PAID, PLAN_DAYS, PLANS

API = "https://api.razorpay.com/v1"


class BillingError(Exception):
    pass


def configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def create_order(store: Store, user: dict, plan: str, http=None) -> dict:
    http = http or httpx
    if plan not in PAID:
        raise BillingError("unknown plan")
    amount = PLANS[plan]["price_paise"]
    receipt = uuid.uuid4().hex[:20]
    r = http.post(f"{API}/orders", auth=(settings.razorpay_key_id, settings.razorpay_key_secret), timeout=20,
                  json={"amount": amount, "currency": "INR", "receipt": receipt,
                        # Notes let you find the customer in the Razorpay dashboard - an opaque ID, not the email.
                        "notes": {"user_id": user["id"], "plan": plan}})
    if r.status_code >= 300:
        raise BillingError("payment provider error")
    order = r.json()
    with store.tx() as c:
        c.execute("INSERT INTO payments (id, user_id, plan, amount_paise, currency, razorpay_order_id, status, created_at) "
                  "VALUES (?, ?, ?, ?, 'INR', ?, 'created', ?)",
                  (receipt, user["id"], plan, amount, order["id"], int(time.time())))
    return {"key_id": settings.razorpay_key_id, "order_id": order["id"], "amount": amount, "currency": "INR",
            "plan": plan, "plan_name": PLANS[plan]["name"], "email": user["email"]}


def checkout_signature_ok(order_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(settings.razorpay_key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def webhook_signature_ok(body: bytes, signature: str) -> bool:
    if not settings.razorpay_webhook_secret:
        return False
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def mark_paid(store: Store, order_id: str, payment_id: str, expected_user_id: str | None = None) -> dict | None:
    """Mark an order paid and extend the owner's plan. Idempotent. Returns the payment row, or None if the
    order is unknown or belongs to a different user than the one confirming it."""
    with store.tx() as c:
        pay = c.execute("SELECT * FROM payments WHERE razorpay_order_id = ?", (order_id,)).fetchone()
        if not pay or (expected_user_id and pay["user_id"] != expected_user_id):
            return None
        if pay["status"] == "paid":
            return dict(pay)
        now = int(time.time())
        c.execute("UPDATE payments SET status = 'paid', razorpay_payment_id = ?, paid_at = ? WHERE id = ?",
                  (payment_id, now, pay["id"]))
        user = c.execute("SELECT plan, plan_expires_at FROM users WHERE id = ?", (pay["user_id"],)).fetchone()
        # Stack time if they renew early on the same plan; switching plans starts fresh from now.
        start = max(now, user["plan_expires_at"] or 0) if user["plan"] == pay["plan"] else now
        c.execute("UPDATE users SET plan = ?, plan_expires_at = ? WHERE id = ?",
                  (pay["plan"], start + PLAN_DAYS * 86400, pay["user_id"]))
        return dict(c.execute("SELECT * FROM payments WHERE id = ?", (pay["id"],)).fetchone())


def mark_failed(store: Store, order_id: str) -> None:
    with store.tx() as c:
        c.execute("UPDATE payments SET status = 'failed' WHERE razorpay_order_id = ? AND status = 'created'", (order_id,))


def handle_webhook(store: Store, event: dict) -> str:
    event_id = event.get("id") or ""
    with store.tx() as c:
        if c.execute("SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone():
            return "duplicate"
        c.execute("INSERT INTO webhook_events (event_id, received_at) VALUES (?, ?)", (event_id, int(time.time())))
    kind = event.get("event", "")
    payment = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
    order_id, payment_id = payment.get("order_id"), payment.get("id")
    if not order_id:
        return "ignored"
    if kind in ("payment.captured", "order.paid"):
        return "paid" if mark_paid(store, order_id, payment_id) else "unknown_order"
    if kind == "payment.failed":
        mark_failed(store, order_id)
        return "failed"
    return "ignored"


def history(store: Store, user_id: str) -> list[dict]:
    """Only this user's payments - the WHERE user_id clause is the segregation boundary."""
    return store.all("SELECT plan, amount_paise, currency, status, razorpay_payment_id, created_at, paid_at "
                     "FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", user_id)
