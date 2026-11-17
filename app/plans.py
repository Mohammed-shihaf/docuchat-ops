"""Plans, prices and limits - the single source of truth for both the API and the pricing page."""
import time

PLANS: dict[str, dict] = {
    # Anonymous visitors: no account at all. Limits apply per session.
    "anonymous": {"name": "Guest", "price_paise": 0, "questions_per_month": None, "questions_per_session": 15,
                  "files_per_session": 3, "session_hours": 1},
    "free": {"name": "Free", "price_paise": 0, "questions_per_month": 100, "questions_per_session": None,
             "files_per_session": 5, "session_hours": 2},
    "pro": {"name": "Pro", "price_paise": 29900, "questions_per_month": 1000, "questions_per_session": None,
            "files_per_session": 20, "session_hours": 8},
    "team": {"name": "Team", "price_paise": 99900, "questions_per_month": 5000, "questions_per_session": None,
             "files_per_session": 50, "session_hours": 24},
}
PAID = ("pro", "team")
PLAN_DAYS = 30


def effective_plan(user: dict | None, now: float | None = None) -> str:
    """A paid plan only counts while it hasn't expired; afterwards the user falls back to Free."""
    if not user:
        return "anonymous"
    now = time.time() if now is None else now
    if user["plan"] in PAID and (user.get("plan_expires_at") or 0) > now:
        return user["plan"]
    return "free"


def public_plans() -> list[dict]:
    return [{"id": k, **v} for k, v in PLANS.items() if k != "anonymous"]
