"""Phase-2 API surface: cap chips, insights, analyze cooldown, dismiss scoping,
explore cap, billing, and the refresh-runs-agents wiring. Auth is off locally, so every
request is the LOCAL_USER_ID unless we write another user's rows directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.main import app
from app.config import LOCAL_USER_ID
from app.market.cache import PriceService
from app.services import analysis_service, briefing_service, dashboard_service
from app.store import agent_repo
from app.store.db import connect
from tests.conftest import FakeModel, FakeProvider, TABLES

PRICES = {"RELIANCE": "1300", "HDFCBANK": "820", "INFY": "1100",
          "MARUTI": "13700", "SUNPHARMA": "1920", "ITC": "280"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_prices", PriceService(FakeProvider(PRICES)))
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))
    yield TestClient(app)
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))


def _seed(client, sample_workbook):
    client.post(
        "/api/portfolio/upload",
        files={"file": ("s.xlsx", sample_workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mode": "replace"},
    )
    client.get("/api/INDIA/dashboard?refresh=true")


# --- cap chips in the dashboard payload ---------------------------------------------


def test_dashboard_carries_cap_class(client, sample_workbook):
    _seed(client, sample_workbook)
    view = client.get("/api/INDIA/dashboard").json()
    reliance = next(s for s in view["stocks"] if s["ticker"] == "RELIANCE")
    assert reliance["cap_class"] in ("large", "mid", "small")
    assert reliance["market_cap"] is not None


# --- analyze (rules always; LLM behind the cooldown) --------------------------------


def test_analyze_runs_rules_and_publishes_insights(client, sample_workbook):
    _seed(client, sample_workbook)
    res = client.post("/api/INDIA/analyze").json()
    assert res["rules_published"] >= 1
    assert res["analyst_ran"] is False  # no LLM key in the test env
    insights = client.get("/api/INDIA/insights").json()
    assert any(i["source"] == "concentration" for i in insights)


def test_analyze_cooldown_skips_the_llm(client, sample_workbook, monkeypatch):
    _seed(client, sample_workbook)
    # Force a model + a recent analyst insight, so the cooldown should bite.
    monkeypatch.setattr(analysis_service, "select_model", lambda plan: FakeModel("groq", [{"insights": []}]))
    with connect() as conn:
        agent_repo.upsert_insight(
            conn, LOCAL_USER_ID, __import__("app.core.sectors", fromlist=["Market"]).Market.INDIA,
            severity="info", title="recent", body="b", source="groq", dedupe_key="analyst:x:y",
        )
    res = client.post("/api/INDIA/analyze").json()
    assert res["analyst_ran"] is False
    assert "refreshed" in (res["skipped_reason"] or "")


# --- dismiss owner-scoping ----------------------------------------------------------


def test_dismiss_is_owner_scoped(client, sample_workbook):
    _seed(client, sample_workbook)
    client.post("/api/INDIA/analyze")
    insight_id = client.get("/api/INDIA/insights").json()[0]["id"]

    # Another user cannot dismiss it — same 404 as a missing id.
    from app.core.sectors import Market
    with connect() as conn:
        assert agent_repo.dismiss_insight(conn, "someone-else", insight_id) is False

    # The owner can.
    assert client.post(f"/api/insights/{insight_id}/dismiss").json()["ok"] is True
    assert client.post("/api/insights/999999/dismiss").status_code == 404


# --- explore daily cap --------------------------------------------------------------


def test_explore_free_cap_enforced(client, monkeypatch):
    # A model that always answers, and a market-cap classification isn't needed here.
    monkeypatch.setattr(analysis_service, "select_model", lambda plan: FakeModel("groq", [
        {"valuation": "v", "profitability": "p", "leverage": "l", "momentum": "m", "overall": "o"}
    ] * 10))
    # Fundamentals from a fake so no network.
    from app.market.fundamentals import Fundamentals
    from decimal import Decimal
    fake = Fundamentals("TEST", "Test Inc", "IT", "INR", Decimal("100"), Decimal("5000000000"),
                        Decimal("20"), Decimal("2"), Decimal("10"), Decimal("30"),
                        Decimal("8"), Decimal("12"), Decimal("1"), Decimal("1"),
                        Decimal("120"), Decimal("80"))
    monkeypatch.setattr(analysis_service._fundamentals, "get_fundamentals", lambda m, t: fake)
    monkeypatch.setattr(analysis_service, "_safe_news", lambda m, t: [])
    from app.config import Settings
    monkeypatch.setattr(analysis_service, "settings", lambda: Settings(free_explore_daily_limit=2))

    # Distinct tickers so each is a cache miss and counts.
    for i, tk in enumerate(["AAA", "BBB", "CCC"]):
        r = client.get(f"/api/INDIA/explore/{tk}").json()
        if i < 2:
            assert r["read"] is not None, r
        else:
            # Third fresh analysis on a 2/day free cap → blocked, ratios still returned.
            assert r["read"] is None
            assert "Free plan" in (r["error"] or "")


def test_explore_cache_hit_is_free_and_does_not_count(client, monkeypatch):
    from app.core.sectors import Market
    with connect() as conn:
        agent_repo.save_analysis(conn, Market.INDIA, "RELIANCE",
                                 {"fundamentals": {"name": "R"}, "read": {"overall": "ok"}}, "groq")
    r = client.get("/api/INDIA/explore/RELIANCE").json()
    assert r["cached"] is True
    assert r["read"]["overall"] == "ok"
    assert r["usage"]["used"] == 0  # a cache hit never touches the counter


# --- billing endpoints --------------------------------------------------------------


def test_billing_flip_via_api(client):
    assert client.get("/api/billing/plan").json()["plan"] == "free"
    assert client.post("/api/billing/checkout").json() == {"mode": "test", "plan": "premium"}
    assert client.get("/api/billing/plan").json()["plan"] == "premium"


# --- report -------------------------------------------------------------------------


def test_report_generate_and_fetch(client, sample_workbook):
    _seed(client, sample_workbook)
    gen = client.post("/api/INDIA/report").json()
    assert "health_score" in gen and gen["red_flags"]
    fetched = client.get("/api/INDIA/report").json()
    assert fetched["health_score"] == gen["health_score"]


# --- briefing (phase 3) -------------------------------------------------------------
# Forced onto the deterministic (no-model) path so the tests are hermetic: no key, no
# network, regardless of what's in .env. That path still runs the whole LangGraph.


def test_briefing_none_before_generation(client, sample_workbook):
    _seed(client, sample_workbook)
    assert client.get("/api/briefing").json() is None


def test_briefing_generate_and_fetch(client, sample_workbook, monkeypatch):
    _seed(client, sample_workbook)
    monkeypatch.setattr(briefing_service, "select_model", lambda plan: None)
    r = client.post("/api/briefing").json()
    assert r["generated"] is True
    b = r["briefing"]
    assert b["headline"] and "markets" in b and b["overall_direction"] in ("positive", "negative", "neutral")
    # The INDIA book was seeded, so at least one market carries sector findings.
    assert any(m["sectors"] for m in b["markets"])
    # GET returns the same persisted document.
    fetched = client.get("/api/briefing").json()
    assert fetched["headline"] == b["headline"]
    assert fetched["generated_by"] == "rules"


def test_briefing_cooldown_skips_second_run(client, sample_workbook, monkeypatch):
    from app.config import settings

    # The default cooldown is now 0 (on-demand regeneration); set one explicitly to exercise
    # the guard that stops a second run from re-billing the agent tree.
    monkeypatch.setattr(settings(), "briefing_cooldown_minutes", 720, raising=False)
    _seed(client, sample_workbook)
    monkeypatch.setattr(briefing_service, "select_model", lambda plan: None)
    assert client.post("/api/briefing").json()["generated"] is True
    second = client.post("/api/briefing").json()
    assert second["generated"] is False
    assert "regenerate" in (second["skipped_reason"] or "")
    assert second["briefing"] is not None  # the existing briefing is still returned
