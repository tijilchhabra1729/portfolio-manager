"""Plans and billing, behind one toggle.

`STRIPE_ENABLED` splits every call into a test path and a live path, and nothing else in
the app knows which is active — callers ask "what plan is this user on?" and "upgrade
this user", and get an answer either way.

- **Test mode** (`false`): `checkout` flips the caller's plan in the database directly, so
  the premium experience is exercisable with no Stripe account. This flip is reachable
  ONLY in test mode; in live mode it raises, because a self-serve free upgrade would be a
  hole.
- **Live mode** (`true`): `checkout` creates a real Stripe Checkout Session and the plan
  only changes when Stripe's signed webhook says the subscription is active.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

from app.config import settings
from app.store import agent_repo

log = logging.getLogger(__name__)

FREE = "free"
PREMIUM = "premium"


class BillingError(Exception):
    """A billing action that can't proceed (e.g. the local flip attempted in live mode)."""


def get_plan(conn: Connection, user_id: str) -> str:
    return agent_repo.get_plan(conn, user_id)


def is_premium(conn: Connection, user_id: str) -> bool:
    return get_plan(conn, user_id) == PREMIUM


# --- test mode --------------------------------------------------------------------


def flip_plan(conn: Connection, user_id: str) -> str:
    """Toggle free⇄premium in the DB. Test mode only — the caller must gate on
    STRIPE_ENABLED, and this double-checks so it can never run live."""
    if settings().stripe_enabled:
        raise BillingError("the local plan flip is disabled while Stripe is live")
    current = get_plan(conn, user_id)
    target = FREE if current == PREMIUM else PREMIUM
    agent_repo.set_plan(conn, user_id, target, status="test-mode")
    log.info("test-mode plan flip: %s -> %s for %s", current, target, user_id)
    return target


# --- live mode --------------------------------------------------------------------


def create_checkout(conn: Connection, user_id: str, email: str | None) -> str:
    """Create a Stripe Checkout Session and return its URL. Live mode only."""
    cfg = settings()
    if not cfg.stripe_enabled:
        raise BillingError("Stripe is not enabled")
    if not (cfg.stripe_secret_key and cfg.stripe_price_id):
        raise BillingError("Stripe is enabled but STRIPE_SECRET_KEY / STRIPE_PRICE_ID are unset")

    import stripe

    stripe.api_key = cfg.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": cfg.stripe_price_id, "quantity": 1}],
        # client_reference_id is how the webhook maps the payment back to our user.
        client_reference_id=user_id,
        customer_email=email or None,
        success_url=f"{cfg.public_url}/?upgraded=1",
        cancel_url=f"{cfg.public_url}/?upgrade=cancelled",
    )
    return session.url


def create_portal(conn: Connection, user_id: str) -> str:
    """A Stripe customer-portal link so the user can cancel/manage. Live mode only."""
    cfg = settings()
    if not cfg.stripe_enabled:
        raise BillingError("Stripe is not enabled")
    row = agent_repo.get_plan_row(conn, user_id)
    customer = row and row.get("stripe_customer_id")
    if not customer:
        raise BillingError("no Stripe customer on record for this user")

    import stripe

    stripe.api_key = cfg.stripe_secret_key
    portal = stripe.billing_portal.Session.create(
        customer=customer, return_url=f"{cfg.public_url}/"
    )
    return portal.url


def handle_webhook(conn: Connection, payload: bytes, signature: str) -> str:
    """Verify a Stripe webhook and apply the plan change. Returns the event type handled.

    Verification is mandatory — the endpoint is unauthenticated by design (Stripe calls
    it), so the signature is the only thing proving the event is real. A bad signature
    raises, which the route turns into a 400.
    """
    cfg = settings()
    if not (cfg.stripe_enabled and cfg.stripe_webhook_secret):
        raise BillingError("webhooks are not configured")

    import stripe

    try:
        event = stripe.Webhook.construct_event(payload, signature, cfg.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise BillingError(f"bad webhook signature: {exc}") from exc

    kind = event["type"]
    obj = event["data"]["object"]

    if kind == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        if user_id:
            agent_repo.set_plan(
                conn, user_id, PREMIUM,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
                status="active",
            )
    elif kind in ("customer.subscription.deleted", "customer.subscription.updated"):
        status = obj.get("status")
        # A subscription that's canceled/unpaid/past_due drops the user to free.
        if kind == "customer.subscription.deleted" or status in ("canceled", "unpaid"):
            user_id = agent_repo.find_user_by_customer(conn, obj.get("customer"))
            if user_id:
                agent_repo.set_plan(conn, user_id, FREE, status=status or "canceled")

    return kind
