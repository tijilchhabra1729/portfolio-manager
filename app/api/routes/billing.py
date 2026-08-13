"""Billing: plan lookup, upgrade (test flip or Stripe Checkout), portal, webhook."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.supabase_jwt import current_user
from app.config import settings
from app.services import billing_service
from app.services.billing_service import BillingError
from app.store.db import connect

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/plan")
def plan(user_id: str = Depends(current_user)):
    with connect() as conn:
        current = billing_service.get_plan(conn, user_id)
    return {"plan": current, "stripe_enabled": settings().stripe_enabled}


@router.post("/checkout")
def checkout(user_id: str = Depends(current_user)):
    """Test mode flips the plan and returns it; live mode returns a Stripe Checkout URL.
    The frontend branches on which key comes back."""
    with connect() as conn:
        if not settings().stripe_enabled:
            new_plan = billing_service.flip_plan(conn, user_id)
            return {"mode": "test", "plan": new_plan}
        try:
            url = billing_service.create_checkout(conn, user_id, email=None)
        except BillingError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"mode": "stripe", "url": url}


@router.post("/portal")
def portal(user_id: str = Depends(current_user)):
    if not settings().stripe_enabled:
        raise HTTPException(404, "portal is only available with Stripe enabled")
    with connect() as conn:
        try:
            url = billing_service.create_portal(conn, user_id)
        except BillingError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"url": url}


@router.post("/webhook")
async def webhook(request: Request):
    """Stripe's signed callback. Unauthenticated by design — the signature is the proof.
    404 when Stripe is off so the endpoint doesn't exist in test mode."""
    if not settings().stripe_enabled:
        raise HTTPException(404, "webhooks are not enabled")

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    with connect() as conn:
        try:
            kind = billing_service.handle_webhook(conn, payload, signature)
        except BillingError as exc:
            # A bad signature is a 400, not a 500 — it's a rejected request, not a crash.
            raise HTTPException(400, str(exc)) from exc
    return {"received": True, "type": kind}
