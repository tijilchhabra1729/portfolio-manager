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
from app.store.db import connect, create_all

TABLES = (
    "transactions",
    "instruments",
    "price_snapshots",
    "portfolio_snapshots",
    "insights",
)


@pytest.fixture(scope="session", autouse=True)
def schema():
    create_all()


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

    def __init__(self, prices: dict[str, str] | None = None) -> None:
        self.prices = prices or {}
        self.calls: list[tuple[Market, tuple[str, ...]]] = []

    def get_quotes(self, market: Market, tickers: Sequence[str]) -> dict[str, Quote]:
        self.calls.append((market, tuple(tickers)))
        now = datetime.now(UTC)
        return {
            t: Quote(t, Decimal(self.prices[t]), now)
            for t in tickers
            if t in self.prices
        }


@pytest.fixture
def sample_workbook(tmp_path) -> bytes:
    return build_workbook(tmp_path / "sample.xlsx", samples=True).read_bytes()


@pytest.fixture
def blank_workbook(tmp_path) -> bytes:
    return build_workbook(tmp_path / "blank.xlsx", samples=False).read_bytes()
