from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pytest
from sqlalchemy import text

from app.core.models import Quote
from app.core.sectors import Market
from app.ingest.template_writer import build_workbook
from app.store.db import connect, create_all, engine

TABLES = (
    "transactions",
    "instruments",
    "price_snapshots",
    "portfolio_snapshots",
    "insights",
    "user_plans",
    "reports",
    "stock_analyses",
    "explore_usage",
    "briefings",
)

# This suite TRUNCATEs every table. Pointed at a real database it would silently destroy
# a live portfolio -- and DATABASE_URL is a single env var that gets flipped to Supabase
# whenever you run a migration against production. Refuse to run anywhere but a local
# database, so the accident is impossible rather than merely unlikely.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "postgres", "db")


@pytest.fixture(scope="session", autouse=True)
def schema():
    host = engine().url.host or ""
    if host not in LOCAL_HOSTS:
        pytest.exit(
            f"\n\nRefusing to run: DATABASE_URL points at '{host}', not a local database."
            f"\nThese tests TRUNCATE every table. Running them here would destroy real data."
            f"\n\nPoint DATABASE_URL back at the local Postgres and try again:"
            f"\n  DATABASE_URL=postgresql+psycopg://pm:pm@localhost:5433/portfolio\n",
            returncode=2,
        )
    create_all()


@pytest.fixture(autouse=True)
def _no_live_llm_keys(monkeypatch):
    """Keep the suite hermetic. A real GROQ/ANTHROPIC key in .env must never make an agent
    reach the network during tests: blank the keys on the cached Settings so
    `select_model()` finds none unless a test injects a FakeModel explicitly. Reverted after
    each test by monkeypatch."""
    from app.config import settings

    s = settings()
    monkeypatch.setattr(s, "groq_api_key", "", raising=False)
    monkeypatch.setattr(s, "gemini_api_key", "", raising=False)
    monkeypatch.setattr(s, "anthropic_api_key", "", raising=False)


@pytest.fixture(autouse=True)
def _offline_briefing_providers(monkeypatch):
    """The briefing graph falls back to real Google News / yfinance providers when a caller
    doesn't inject its own. Point the module defaults at offline stubs so no test — including
    the API/service ones that run the whole graph with model=None — reaches the network."""
    from app.agents.briefing import graph as _graph

    class _NoNews:
        def get_news(self, *args, **kwargs):
            return []

    class _NoFundamentals:
        def get_fundamentals(self, *args, **kwargs):
            return None

    monkeypatch.setattr(_graph, "_default_news", _NoNews(), raising=False)
    monkeypatch.setattr(_graph, "_default_fundamentals", _NoFundamentals(), raising=False)


@pytest.fixture
def conn():
    """One transaction per test, rolled back afterwards, so tests cannot see each
    other's writes and nothing survives the run."""
    with connect() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))
        yield connection
        connection.rollback()


class FakeProvider:
    """A provider that answers from a dict. Tests must never depend on Yahoo being up,
    or on what Reliance happens to be trading at today."""

    # A big default cap so `cap_class` classifies as large in tests; pass `caps` to
    # override per ticker (e.g. to exercise the small-cap agent).
    DEFAULT_CAP = Decimal("5000000000000")

    def __init__(
        self, prices: dict[str, str] | None = None, caps: dict[str, str] | None = None
    ) -> None:
        self.prices = prices or {}
        self.caps = caps or {}
        self.calls: list[tuple[Market, tuple[str, ...]]] = []

    def get_quotes(self, market: Market, tickers: Sequence[str]) -> dict[str, Quote]:
        self.calls.append((market, tuple(tickers)))
        now = datetime.now(UTC)
        return {
            t: Quote(
                t, Decimal(self.prices[t]), now,
                market_cap=Decimal(self.caps.get(t, self.DEFAULT_CAP)),
            )
            for t in tickers
            if t in self.prices
        }


class FakeModel:
    """Stands in for an AnalystModel. Returns a queued dict (or raises a queued error),
    so tests exercise the agents without a network or a key."""

    def __init__(self, name: str = "groq", replies=None):
        self.name = name
        self._replies = list(replies or [])
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500) -> dict:
        self.calls.append((system, user))
        reply = self._replies.pop(0) if self._replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def sample_workbook(tmp_path) -> bytes:
    return build_workbook(tmp_path / "sample.xlsx", samples=True).read_bytes()


@pytest.fixture
def blank_workbook(tmp_path) -> bytes:
    return build_workbook(tmp_path / "blank.xlsx", samples=False).read_bytes()
