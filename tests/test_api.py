"""API tests. These hit the real routes, which open their own transactions, so the
database is truncated around each test rather than rolled back."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.main import app
from app.config import settings
from app.market.cache import PriceService
from app.services import dashboard_service
from app.store.db import connect
from tests.conftest import TABLES, FakeProvider

# Read the token the app is actually configured with, rather than hardcoding the
# placeholder from .env.example. Anyone who generates a real REFRESH_TOKEN -- which is
# exactly what deploying tells you to do -- would otherwise watch these tests start
# failing with a 401 for no visible reason.
CRON = {"Authorization": f"Bearer {settings().refresh_token}"}

PRICES = {
    "RELIANCE": "1300", "HDFCBANK": "820", "INFY": "1100",
    "MARUTI": "13700", "SUNPHARMA": "1920", "ITC": "280",
    "AAPL": "315", "MSFT": "385", "JPM": "336", "JNJ": "257",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_prices", PriceService(FakeProvider(PRICES)))
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))
    yield TestClient(app)
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))


def upload(client, content: bytes, mode: str = "replace"):
    return client.post(
        "/api/portfolio/upload",
        files={"file": ("sample.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mode": mode},
    )


def test_health_and_markets(client):
    assert client.get("/healthz").json() == {"ok": True}
    markets = client.get("/api/portfolio/markets").json()
    assert {m["code"] for m in markets} == {"INDIA", "US"}
    india = next(m for m in markets if m["code"] == "INDIA")
    assert india["currency"] == "INR"
    assert "Banking" in india["sectors"]


def test_index_serves_the_dashboard(client):
    body = client.get("/").text
    assert "Portfolio" in body
    assert "/static/app.js" in body


def test_template_downloads(client):
    for kind in ("blank", "sample"):
        res = client.get(f"/api/portfolio/template?kind={kind}")
        assert res.status_code == 200
        assert res.content[:2] == b"PK"  # a real xlsx is a zip


def test_upload_then_dashboard(client, sample_workbook):
    res = upload(client, sample_workbook)
    assert res.status_code == 200, res.text
    assert res.json()["transactions_added"] == 10

    view = client.get("/api/INDIA/dashboard").json()
    assert view["currency"] == "INR"
    assert view["totals"]["stock_count"] == 6
    assert view["unpriced"] == []

    reliance = next(s for s in view["stocks"] if s["ticker"] == "RELIANCE")
    assert reliance["invested"] == "59000.00"
    assert reliance["market_value"] == "65000.00"
    assert reliance["pnl"] == "6000.00"


def test_money_crosses_the_wire_as_strings_not_floats(client, sample_workbook):
    """A JSON number is an IEEE double. Serialising money as a number would undo the
    exactness the backend keeps all the way down."""
    upload(client, sample_workbook)
    view = client.get("/api/INDIA/dashboard").json()
    for row in view["stocks"]:
        assert isinstance(row["invested"], str)
        assert isinstance(row["allocation_pct"], str)
    assert Decimal(view["totals"]["invested"]) > 0


def test_allocation_sums_to_one_hundred(client, sample_workbook):
    upload(client, sample_workbook)
    for market in ("INDIA", "US"):
        view = client.get(f"/api/{market}/dashboard").json()
        total = sum(Decimal(s["allocation_pct"]) for s in view["stocks"])
        assert abs(total - 100) <= Decimal("0.06"), market


def test_bad_upload_returns_row_level_errors_and_saves_nothing(client, sample_workbook):
    from tests.test_services import edit

    broken = edit(sample_workbook, "India_Holdings", 2, 6, "Crypto")
    res = upload(client, broken)
    assert res.status_code == 422

    errors = res.json()["detail"]["errors"]
    assert errors[0]["row"] == 2
    assert errors[0]["column"] == "Sector"

    view = client.get("/api/INDIA/dashboard").json()
    assert view["totals"]["stock_count"] == 0


def test_non_xlsx_is_rejected(client):
    res = client.post(
        "/api/portfolio/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"mode": "append"},
    )
    assert res.status_code == 400


def test_delete_flow(client, sample_workbook):
    upload(client, sample_workbook)
    res = client.post(
        "/api/portfolio/delete",
        files={"file": ("d.xlsx", sample_workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["removed"][0]["units_left"] == "5"

    view = client.get("/api/INDIA/dashboard").json()
    maruti = next(s for s in view["stocks"] if s["ticker"] == "MARUTI")
    assert maruti["units"] == "5"


def test_excel_export(client, sample_workbook):
    upload(client, sample_workbook)
    res = client.get("/api/INDIA/export")
    assert res.status_code == 200
    assert res.content[:2] == b"PK"
    assert "portfolio_dashboard_india.xlsx" in res.headers["content-disposition"]


# --- the agent seam -----------------------------------------------------------------


def test_insights_endpoint_is_live_and_empty(client):
    """Returns [] today. An agent that writes an insights row appears in the UI with no
    API or frontend change at all -- which is the whole point of building it now."""
    assert client.get("/api/INDIA/insights").json() == []


def test_an_agent_written_insight_surfaces_immediately(client):
    from app.config import LOCAL_USER_ID
    from app.core.sectors import Market
    from app.store import repository

    with connect() as conn:
        repository.add_insight(
            conn, LOCAL_USER_ID, Market.INDIA,
            severity="critical",
            title="Banking is 32% of the portfolio",
            body="Above the 25% single-sector guideline.",
            source="concentration-agent",
            related_sector="Banking",
        )

    rows = client.get("/api/INDIA/insights").json()
    assert len(rows) == 1
    assert rows[0]["source"] == "concentration-agent"
    assert rows[0]["related_sector"] == "Banking"


def test_refresh_endpoint_requires_the_token(client, sample_workbook):
    assert client.post("/api/refresh").status_code == 401
    assert client.post("/api/refresh", headers={"Authorization": "Bearer wrong"}).status_code == 401

    upload(client, sample_workbook)
    res = client.post("/api/refresh", headers=CRON)
    assert res.status_code == 200
    assert {s["market"] for s in res.json()["snapshots"]} == {"INDIA", "US"}


def test_refresh_snapshots_whoever_actually_holds_stock(client, sample_workbook):
    """A cron request has no logged-in user. In production holdings belong to a Supabase
    UUID, so the job must discover its users rather than assume one -- otherwise it
    happily snapshots an empty portfolio every night and the agent layer inherits months
    of zeroes."""
    from app.core.sectors import Market
    from app.services import portfolio_service
    from app.services.portfolio_service import UploadMode

    supabase_uuid = "8f14e45f-ceea-467a-9575-2c5d4a1b2c3d"
    with connect() as conn:
        portfolio_service.upload(conn, supabase_uuid, sample_workbook, UploadMode.REPLACE)

    res = client.post("/api/refresh", headers=CRON)
    snapshots = res.json()["snapshots"]

    assert {s["user"] for s in snapshots} == {supabase_uuid}
    india = next(s for s in snapshots if s["market"] == "INDIA")
    assert india["stocks"] == 6  # the real portfolio, not an empty "local" one


def test_refresh_writes_nothing_when_no_one_holds_anything(client):
    res = client.post("/api/refresh", headers=CRON)
    assert res.status_code == 200
    assert res.json()["snapshots"] == []


def test_refresh_writes_history_for_the_agent_layer(client, sample_workbook):
    upload(client, sample_workbook)
    client.post("/api/refresh", headers=CRON)

    history = client.get("/api/INDIA/history").json()
    assert len(history) == 1
    assert Decimal(history[0]["invested"]) > 0
    assert "Banking" in history[0]["sector_allocations"]
