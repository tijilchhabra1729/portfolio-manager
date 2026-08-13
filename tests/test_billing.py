"""Billing: the test-mode flip, the live-mode guard, and webhook verification."""

from __future__ import annotations

import time

import pytest

from app.services import billing_service
from app.services.billing_service import BillingError
from app.store import agent_repo

USER = "u1"


def _settings(monkeypatch, **over):
    from app.config import Settings, settings
    settings.cache_clear()
    base = {"stripe_enabled": False}
    monkeypatch.setattr(billing_service, "settings", lambda: Settings(**base | over))


def test_no_plan_row_reads_as_free(conn):
    assert billing_service.get_plan(conn, USER) == "free"


def test_test_mode_flip_toggles(conn, monkeypatch):
    _settings(monkeypatch, stripe_enabled=False)
    assert billing_service.flip_plan(conn, USER) == "premium"
    assert billing_service.get_plan(conn, USER) == "premium"
    assert billing_service.flip_plan(conn, USER) == "free"


def test_flip_is_blocked_in_live_mode(conn, monkeypatch):
    # The whole safety of the toggle: the free self-upgrade must be impossible live.
    _settings(monkeypatch, stripe_enabled=True)
    with pytest.raises(BillingError):
        billing_service.flip_plan(conn, USER)


def test_webhook_rejects_a_bad_signature(conn, monkeypatch):
    _settings(monkeypatch, stripe_enabled=True, stripe_webhook_secret="whsec_test")
    with pytest.raises(BillingError):
        billing_service.handle_webhook(conn, b'{"type":"x"}', "t=1,v1=deadbeef")


def _signed(secret: str, payload: bytes) -> str:
    import hashlib
    import hmac

    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_completed_makes_premium(conn, monkeypatch):
    secret = "whsec_test"
    _settings(monkeypatch, stripe_enabled=True, stripe_webhook_secret=secret)
    payload = (
        b'{"type":"checkout.session.completed","data":{"object":'
        b'{"client_reference_id":"u1","customer":"cus_1","subscription":"sub_1"}}}'
    )
    billing_service.handle_webhook(conn, payload, _signed(secret, payload))
    assert billing_service.get_plan(conn, "u1") == "premium"


def test_webhook_subscription_deleted_drops_to_free(conn, monkeypatch):
    secret = "whsec_test"
    _settings(monkeypatch, stripe_enabled=True, stripe_webhook_secret=secret)
    # Seed a premium user with a known customer id.
    agent_repo.set_plan(conn, "u1", "premium", stripe_customer_id="cus_9", status="active")

    payload = b'{"type":"customer.subscription.deleted","data":{"object":{"customer":"cus_9","status":"canceled"}}}'
    billing_service.handle_webhook(conn, payload, _signed(secret, payload))
    assert billing_service.get_plan(conn, "u1") == "free"
