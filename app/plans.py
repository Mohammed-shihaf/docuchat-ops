"""Plans, prices, limits and features - the single source of truth for the API and the pricing page.

Using DocuChat requires signing in. The Free tier is deliberately small - enough to try the product,
not enough to rely on it. Diagrams and exact-passage citations are paid features.
"""
import time

PLANS: dict[str, dict] = {
    "free": {"name": "Free", "price_paise": 0, "questions_per_month": 20,
             "files_per_session": 2, "session_hours": 1, "diagrams": False, "passages": False},
    "pro": {"name": "Pro", "price_paise": 29900, "questions_per_month": 1000,
            "files_per_session": 20, "session_hours": 8, "diagrams": True, "passages": True},
    "team": {"name": "Team", "price_paise": 99900, "questions_per_month": 5000,
             "files_per_session": 50, "session_hours": 24, "diagrams": True, "passages": True},
}
PAID = ("pro", "team")
PLAN_DAYS = 30


def effective_plan(user: dict, now: float | None = None) -> str:
    """A paid plan only counts while it hasn't expired; afterwards the user falls back to Free."""
    now = time.time() if now is None else now
    if user["plan"] in PAID and (user.get("plan_expires_at") or 0) > now:
        return user["plan"]
    return "free"


def public_plans() -> list[dict]:
    return [{"id": k, **v} for k, v in PLANS.items()]
