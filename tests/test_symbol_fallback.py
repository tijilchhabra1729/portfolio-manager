"""Exchange fallback, and the guard that makes it safe.

A ticker that misses on NSE is retried on BSE. But ticker symbols are not globally
unique, so the retry must never wander out of the market it belongs to -- and even within
one, the currency is checked, because a confidently wrong price is far worse than a
missing one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.sectors import Market, yf_candidates
from app.market.yfinance_provider import YFinanceProvider


class FakeFastInfo(dict):
    """Mimics yfinance's FastInfo, including the fact that a missing key raises."""

    def __getitem__(self, key):
        if key not in self:
            raise KeyError(key)
        return super().__getitem__(key)


class FakeBatch:
    def __init__(self, quotes: dict[str, dict]) -> None:
        self.tickers = {
            symbol: type("T", (), {"fast_info": FakeFastInfo(info)})()
            for symbol, info in quotes.items()
        }


@pytest.fixture
def yahoo(monkeypatch):
    """Stand in for Yahoo. Any symbol not listed simply has no data."""
    state = {"quotes": {}, "calls": []}

    def fake_tickers(joined: str):
        requested = joined.split()
        state["calls"].append(tuple(requested))
        return FakeBatch({s: state["quotes"][s] for s in requested if s in state["quotes"]})

    monkeypatch.setattr("app.market.yfinance_provider.yf.Tickers", fake_tickers)
    return state


def quote(price: float, currency: str = "INR") -> dict:
    return {"lastPrice": price, "currency": currency, "marketCap": 1_000_000.0}


# --- candidates ----------------------------------------------------------------------


def test_india_tries_nse_then_bse():
    assert yf_candidates("ANGELONE", Market.INDIA) == ["ANGELONE.NS", "ANGELONE.BO"]


def test_us_has_no_cross_market_fallback():
    """The fallback must not reach into another country's exchange. IEX is Indian Energy
    Exchange on the NSE and an energy company on the NASDAQ."""
    assert yf_candidates("IEX", Market.US) == ["IEX"]


# --- fallback ------------------------------------------------------------------------


def test_a_symbol_missing_from_nse_is_retried_on_bse(yahoo):
    yahoo["quotes"] = {"ANGELONE.BO": quote(340.25)}  # not on NSE

    quotes = YFinanceProvider().get_quotes(Market.INDIA, ["ANGELONE"])

    assert quotes["ANGELONE"].price == Decimal("340.25")
    assert quotes["ANGELONE"].ticker == "ANGELONE"  # the plain symbol, not ANGELONE.BO
    assert yahoo["calls"] == [("ANGELONE.NS",), ("ANGELONE.BO",)]


def test_nse_wins_when_both_exchanges_have_it(yahoo):
    yahoo["quotes"] = {"RELIANCE.NS": quote(1296.90), "RELIANCE.BO": quote(1290.00)}

    quotes = YFinanceProvider().get_quotes(Market.INDIA, ["RELIANCE"])

    assert quotes["RELIANCE"].price == Decimal("1296.90")
    assert yahoo["calls"] == [("RELIANCE.NS",)]  # never asked BSE


def test_only_the_failures_are_retried(yahoo):
    """The second pass must not re-request the tickers that already worked."""
    yahoo["quotes"] = {"RELIANCE.NS": quote(1296.90), "ANGELONE.BO": quote(340.25)}

    quotes = YFinanceProvider().get_quotes(Market.INDIA, ["RELIANCE", "ANGELONE"])

    assert set(quotes) == {"RELIANCE", "ANGELONE"}
    assert yahoo["calls"][0] == ("RELIANCE.NS", "ANGELONE.NS")
    assert yahoo["calls"][1] == ("ANGELONE.BO",)  # RELIANCE not asked again


def test_a_symbol_on_neither_exchange_has_no_price(yahoo):
    """No price is the honest answer. The dashboard shows the row as unpriced rather than
    inventing a number."""
    yahoo["quotes"] = {}
    assert YFinanceProvider().get_quotes(Market.INDIA, ["NOTREAL"]) == {}


# --- the currency guard --------------------------------------------------------------


def test_a_quote_in_the_wrong_currency_is_refused(yahoo):
    """The whole reason the fallback is safe.

    Suppose a US holding's symbol resolved to something quoted in rupees. Accepting it
    would price the position with an Indian quote and render it with a dollar sign -- and
    the P/L would look entirely plausible. Drop it instead.
    """
    yahoo["quotes"] = {"IEX": quote(150.0, currency="INR")}

    quotes = YFinanceProvider().get_quotes(Market.US, ["IEX"])
    assert quotes == {}


def test_the_right_currency_is_accepted(yahoo):
    yahoo["quotes"] = {"AAPL": quote(315.32, currency="USD")}
    quotes = YFinanceProvider().get_quotes(Market.US, ["AAPL"])
    assert quotes["AAPL"].price == Decimal("315.32")


def test_a_quote_with_no_currency_is_trusted(yahoo):
    """Yahoo occasionally omits it. Not grounds to discard an otherwise good price."""
    yahoo["quotes"] = {"RELIANCE.NS": {"lastPrice": 1296.90, "currency": None, "marketCap": None}}
    quotes = YFinanceProvider().get_quotes(Market.INDIA, ["RELIANCE"])
    assert quotes["RELIANCE"].price == Decimal("1296.90")


# --- float hygiene, still ------------------------------------------------------------


def test_float_noise_is_quantized_away(yahoo):
    yahoo["quotes"] = {"RELIANCE.NS": quote(1296.800048828125)}
    quotes = YFinanceProvider().get_quotes(Market.INDIA, ["RELIANCE"])
    assert quotes["RELIANCE"].price == Decimal("1296.80")


def test_one_dead_symbol_does_not_sink_the_batch(yahoo):
    yahoo["quotes"] = {"RELIANCE.NS": quote(1296.90), "INFY.NS": quote(1101.90)}

    quotes = YFinanceProvider().get_quotes(
        Market.INDIA, ["RELIANCE", "DELISTED", "INFY"]
    )
    assert set(quotes) == {"RELIANCE", "INFY"}
