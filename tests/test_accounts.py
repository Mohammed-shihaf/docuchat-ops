"""Accounts, per-user segregation, quotas and Razorpay billing."""
import dataclasses
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.billing
import app.main
from app import auth
from app.main import app as api
from tests.test_app import upload


def signed_in_client(store, email: str) -> TestClient:
    """A separate browser for a separate person: its own cookie jar."""
    user = store.get_or_create_user(email)
    c = TestClient(api)
    c.cookies.set(auth.COOKIE, auth.start_auth_session(store, user["id"]))
    return c


@pytest.fixture
def two_users(client, store):
    # `client` installs the fakes; build two independent signed-in browsers on top.
    return signed_in_client(store, "asha@example.com"), signed_in_client(store, "ravi@example.com")


# ---------------------------------------------------------------- sign-in

def test_magic_link_login_flow(client, store, monkeypatch):
    monkeypatch.setattr(app.main, "settings", dataclasses.replace(app.main.settings, dev_login=True, smtp_host=""))
    link = client.post("/auth/login", json={"email": "Asha@Example.com"}).json()["dev_link"]
    token = link.split("token=")[1]
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?login=ok"
    assert "httponly" in r.headers["set-cookie"].lower()
    me = client.get("/me").json()
    assert me["signed_in"] and me["email"] == "asha@example.com" and me["plan"] == "free"
    # The link is single-use.
    assert client.get(f"/auth/verify?token={token}", follow_redirects=False).headers["location"] == "/?login=expired"


def test_only_token_hashes_are_stored(client, store, monkeypatch):
    monkeypatch.setattr(app.main, "settings", dataclasses.replace(app.main.settings, dev_login=True, smtp_host=""))
    token = client.post("/auth/login", json={"email": "a@b.co"}).json()["dev_link"].split("token=")[1]
    rows = store.all("SELECT token_hash FROM login_tokens")
    assert rows and all(token not in r["token_hash"] for r in rows)


def test_invalid_email_rejected(client):
    assert client.post("/auth/login", json={"email": "not-an-email"}).status_code == 422


def test_logout_revokes_cookie(two_users):
    a, _ = two_users
    assert a.get("/me").json()["signed_in"]
    a.post("/auth/logout")
    assert not a.get("/me").json()["signed_in"]


# ---------------------------------------------------------------- segregation

def test_users_cannot_access_each_others_sessions(two_users, client, guest):
    a, b = two_users
    sid = a.post("/sessions").json()["session_id"]
    upload(a, sid, "asha-salary.md", b"Asha earns 92,000 rupees.")
    # Ravi and a third user get 404 even with the exact session ID; a signed-out browser gets 401.
    for other, code in ((b, 404), (client, 404), (guest, 401)):
        assert other.get(f"/sessions/{sid}").status_code == code
        assert other.post(f"/sessions/{sid}/chat", json={"question": "salary?"}).status_code == code
        assert upload(other, sid, "x.md", b"inject").status_code == code
        other.delete(f"/sessions/{sid}")                     # can't delete it either
    assert a.get(f"/sessions/{sid}").json()["documents"] == [{"source": "asha-salary.md", "chunks": 1}]


def test_signed_out_users_cannot_use_the_app(guest):
    assert guest.post("/sessions").status_code == 401
    fake = "a" * 32
    assert guest.get(f"/sessions/{fake}").status_code == 401
    assert upload(guest, fake, "a.md", b"x").status_code == 401
    assert guest.post(f"/sessions/{fake}/chat", json={"question": "hi"}).status_code == 401
    assert guest.get("/me").json() == {"signed_in": False, "payments_enabled": False}


def test_owner_tag_reveals_nothing_and_is_keyed():
    tag = auth.owner_tag("user-123")
    assert len(tag) == 12 and "user-123" not in tag
    assert tag != hashlib.sha256(b"user-123").hexdigest()[:12]   # not guessable without SECRET_KEY


def test_usage_is_counted_per_user(two_users, store):
    a, b = two_users
    sid = a.post("/sessions").json()["session_id"]
    upload(a, sid, "a.md", b"hello")
    for _ in range(3):
        a.post(f"/sessions/{sid}/chat", json={"question": "hi"})
    assert a.get("/me").json()["usage"] == {"questions": 3, "files": 1}
    assert b.get("/me").json()["usage"] == {"questions": 0, "files": 0}


def test_delete_account_erases_records(two_users, store):
    a, b = two_users
    a_id = store.one("SELECT id FROM users WHERE email = 'asha@example.com'")["id"]
    assert a.delete("/me").status_code == 204
    for table in ("users", "auth_sessions", "usage", "payments"):
        col = "id" if table == "users" else "user_id"
        assert store.one(f"SELECT COUNT(*) n FROM {table} WHERE {col} = ?", a_id)["n"] == 0
    assert b.get("/me").json()["signed_in"]                  # other users untouched


# ---------------------------------------------------------------- quotas

def test_monthly_question_quota_per_user(two_users, store):
    a, b = two_users
    a_id = store.one("SELECT id FROM users WHERE email = 'asha@example.com'")["id"]
    with store.tx() as c:
        c.execute("INSERT INTO usage (user_id, period, questions) VALUES (?, ?, 20)", (a_id, store.period()))
    sa = a.post("/sessions").json()["session_id"]
    sb = b.post("/sessions").json()["session_id"]
    upload(a, sa, "a.md", b"x")
    upload(b, sb, "b.md", b"y")
    assert a.post(f"/sessions/{sa}/chat", json={"question": "q"}).status_code == 402
    assert b.post(f"/sessions/{sb}/chat", json={"question": "q"}).status_code == 200   # Ravi unaffected


def test_free_plan_file_limit(two_users):
    a, _ = two_users
    sid = a.post("/sessions").json()["session_id"]
    for i in range(2):
        assert upload(a, sid, f"{i}.md", b"x").status_code == 200
    r = upload(a, sid, "2.md", b"x")
    assert r.status_code == 402 and "upgrade" in r.json()["detail"]


DIAGRAM_REPLY = "Build, then deploy [flow.md]\n```mermaid\nflowchart TD\nA-->B\n```"


def llm_replying(text):
    return lambda model, messages, temperature: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_free_plan_hides_passages_and_diagrams(two_users, rag):
    a, _ = two_users
    sid = a.post("/sessions").json()["session_id"]
    upload(a, sid, "flow.md", b"Build then deploy.")
    rag.llm.chat.completions.create = llm_replying(DIAGRAM_REPLY)   # the model ignores the no-diagram rule
    r = a.post(f"/sessions/{sid}/chat", json={"question": "Explain it with a diagram"}).json()
    assert "mermaid" not in r["answer"] and "```" not in r["answer"]          # stripped server-side anyway
    assert r["diagram_locked"] and r["passages_locked"] and r["citations"] == []
    assert r["sources"] == ["flow.md"]                                        # file names still shown


def test_pro_plan_gets_passages_and_diagrams(client, rag):
    sid = client.post("/sessions").json()["session_id"]                      # `client` is a Pro user
    upload(client, sid, "flow.md", b"Build then deploy.")
    rag.llm.chat.completions.create = llm_replying(DIAGRAM_REPLY)
    r = client.post(f"/sessions/{sid}/chat", json={"question": "Explain it with a diagram"}).json()
    assert "```mermaid" in r["answer"] and r["citations"] and not r["passages_locked"] and not r["diagram_locked"]


# ---------------------------------------------------------------- billing

KEY_SECRET, HOOK_SECRET = "rzp_test_secret", "hook_secret"


@pytest.fixture
def razorpay(monkeypatch):
    cfg = dataclasses.replace(app.main.settings, razorpay_key_id="rzp_test_key",
                              razorpay_key_secret=KEY_SECRET, razorpay_webhook_secret=HOOK_SECRET)
    for mod in (app.main, app.billing):
        monkeypatch.setattr(mod, "settings", cfg)
    counter = {"n": 0}

    def fake_post(url, auth, timeout, json):
        counter["n"] += 1
        return SimpleNamespace(status_code=200, json=lambda: {"id": f"order_{counter['n']}", **json})

    monkeypatch.setattr(app.billing, "httpx", SimpleNamespace(post=fake_post))


def sign(order_id, payment_id):
    return hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def test_checkout_requires_sign_in(guest, razorpay):
    assert guest.post("/billing/checkout", json={"plan": "pro"}).status_code == 401


def test_checkout_and_verify_upgrades_only_that_user(two_users, razorpay):
    a, b = two_users
    order = a.post("/billing/checkout", json={"plan": "pro"}).json()
    assert order["amount"] == 29900 and order["currency"] == "INR"
    r = a.post("/billing/verify", json={"razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_1",
                                        "razorpay_signature": sign(order["order_id"], "pay_1")})
    assert r.json() == {"status": "paid", "plan": "pro"}
    assert a.get("/me").json()["plan"] == "pro"
    assert b.get("/me").json()["plan"] == "free"
    assert [p["status"] for p in a.get("/billing/history").json()["payments"]] == ["paid"]
    assert b.get("/billing/history").json()["payments"] == []          # Ravi sees none of Asha's payments


def test_forged_signature_rejected(two_users, razorpay):
    a, _ = two_users
    order = a.post("/billing/checkout", json={"plan": "pro"}).json()
    r = a.post("/billing/verify", json={"razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_x",
                                        "razorpay_signature": "0" * 64})
    assert r.status_code == 400 and a.get("/me").json()["plan"] == "free"


def test_user_cannot_claim_someone_elses_order(two_users, razorpay):
    a, b = two_users
    order = a.post("/billing/checkout", json={"plan": "team"}).json()
    r = b.post("/billing/verify", json={"razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_2",
                                        "razorpay_signature": sign(order["order_id"], "pay_2")})
    assert r.status_code == 404 and b.get("/me").json()["plan"] == "free"


def test_webhook_is_verified_and_idempotent(two_users, razorpay, client, store):
    a, _ = two_users
    order = a.post("/billing/checkout", json={"plan": "pro"}).json()
    event = {"id": "evt_1", "event": "payment.captured",
             "payload": {"payment": {"entity": {"id": "pay_9", "order_id": order["order_id"]}}}}
    body = json.dumps(event).encode()
    sig = hmac.new(HOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert client.post("/billing/webhook", content=body, headers={"x-razorpay-signature": "bad"}).status_code == 400
    assert client.post("/billing/webhook", content=body, headers={"x-razorpay-signature": sig}).json()["result"] == "paid"
    assert client.post("/billing/webhook", content=body, headers={"x-razorpay-signature": sig}).json()["result"] == "duplicate"
    expires = a.get("/me").json()["plan_expires_at"]
    assert a.get("/me").json()["plan"] == "pro" and expires > time.time() + 29 * 86400


def test_paid_plan_expires_back_to_free(store):
    from app.plans import effective_plan
    user = {"plan": "pro", "plan_expires_at": int(time.time()) - 1}
    assert effective_plan(user) == "free"
    assert effective_plan({**user, "plan_expires_at": int(time.time()) + 60}) == "pro"


def test_payments_disabled_without_keys(two_users):
    a, _ = two_users
    assert a.post("/billing/checkout", json={"plan": "pro"}).status_code == 503


def test_unknown_plan_rejected(two_users, razorpay):
    a, _ = two_users
    assert a.post("/billing/checkout", json={"plan": "platinum"}).status_code == 400
