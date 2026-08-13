"""Fundamentals: the Finnhub keyed fallback maps fields and units correctly, and it only
runs for US symbols with a key. HTTP is mocked, so this is hermetic — the live shape it
mirrors is verified separately with a real key (scripts/one-liner in the PR notes)."""

from __future__ import annotations

from decimal import Decimal

from app.core.sectors import Market
from app.market.fundamentals import FinnhubFundamentals, _has_ratios

# Canned responses shaped like Finnhub's live API.
_PROFILE = {
    "name": "Apple Inc", "finnhubIndustry": "Technology", "currency": "USD",
    "marketCapitalization": 3500000.0,  # millions
}
_METRIC = {"metric": {
    "peTTM": 32.5, "pbAnnual": 48.0, "roeTTM": 137.8, "netProfitMarginTTM": 25.3,
    "totalDebt/totalEquityAnnual": 1.54, "revenueGrowthTTMYoy": 8.1,
    "currentDividendYieldTTM": 0.44, "beta": 1.25, "52WeekHigh": 260.1, "52WeekLow": 164.0,
}}
_QUOTE = {"c": 232.1}


def _provider(monkeypatch) -> FinnhubFundamentals:
    p = FinnhubFundamentals("test-key")
    routes = {"/stock/profile2": _PROFILE, "/stock/metric": _METRIC, "/quote": _QUOTE}
    monkeypatch.setattr(p, "_get", lambda path, params: routes[path])
    return p


def test_finnhub_maps_us_fundamentals(monkeypatch):
    f = _provider(monkeypatch).get_fundamentals(Market.US, "aapl")
    assert f is not None and _has_ratios(f)
    assert f.ticker == "AAPL"
    assert f.name == "Apple Inc"
    assert f.sector == "Technology"
    assert f.price == Decimal("232.10")
    assert f.market_cap == Decimal("3500000000000.00")   # millions -> absolute
    assert f.pe == Decimal("32.50")
    assert f.roe_pct == Decimal("137.80")                # already a %, not scaled again
    assert f.profit_margin_pct == Decimal("25.30")
    assert f.debt_to_equity == Decimal("154.00")         # ratio ×100 to match yfinance
    assert f.week52_high == Decimal("260.10")
    assert f.week52_low == Decimal("164.00")


def test_finnhub_is_us_only_and_needs_a_key(monkeypatch):
    # NSE symbol -> None (free tier is US-listed only); it never even makes a request.
    assert _provider(monkeypatch).get_fundamentals(Market.INDIA, "RELIANCE") is None
    # No key -> None.
    assert FinnhubFundamentals("").get_fundamentals(Market.US, "AAPL") is None
