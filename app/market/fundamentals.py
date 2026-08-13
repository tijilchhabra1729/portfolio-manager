"""Fundamental ratios for one ticker, from yfinance `.info`.

Feeds the Explore tab: valuation (P/E, P/B), profitability (ROE, margin), leverage
(debt/equity), growth (revenue), income (dividend yield), plus name/sector/market cap and
the 52-week range for a momentum read. Everything is optional — `.info` omits fields for
many symbols — so each is `None`-tolerant and the caller shows a dash rather than failing.

Units are normalised here so the UI and the LLM never have to guess: ratios that yfinance
returns as fractions (ROE 0.089) are converted to percentages (8.9). Where a field's unit
is ambiguous across yfinance versions (dividend yield), it is passed through and labelled,
not silently scaled.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import yfinance as yf

from app.core.sectors import Market, yf_symbol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    name: str | None
    sector: str | None
    currency: str | None
    price: Decimal | None
    market_cap: Decimal | None
    pe: Decimal | None            # trailing P/E
    pb: Decimal | None            # price / book
    roe_pct: Decimal | None       # return on equity, %
    debt_to_equity: Decimal | None
    profit_margin_pct: Decimal | None
    revenue_growth_pct: Decimal | None
    dividend_yield_pct: Decimal | None
    beta: Decimal | None
    week52_high: Decimal | None
    week52_low: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        # Decimals to strings so money survives JSON exactly, as everywhere else.
        return {
            k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()
        }


class FundamentalsProvider(Protocol):
    def get_fundamentals(self, market: Market, ticker: str) -> Fundamentals | None:
        ...


def _dec(value: Any, scale: Decimal = Decimal(1)) -> Decimal | None:
    """A raw yfinance number → Decimal, scaled and 2dp, or None. str() first so we never
    bake in float noise (the same discipline the price path uses)."""
    if value is None:
        return None
    try:
        number = (Decimal(str(value)) * scale).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number


class YFinanceFundamentals:
    def get_fundamentals(self, market: Market, ticker: str) -> Fundamentals | None:
        try:
            info = yf.Ticker(yf_symbol(ticker, market)).info or {}
        except Exception:
            log.warning("fundamentals fetch failed for %s", ticker, exc_info=True)
            return None
        if not info.get("longName") and not info.get("shortName") and not info.get("regularMarketPrice"):
            return None  # yfinance returns a near-empty dict for a bad symbol

        HUNDRED = Decimal(100)
        return Fundamentals(
            ticker=ticker.upper(),
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            currency=info.get("currency"),
            price=_dec(info.get("currentPrice") or info.get("regularMarketPrice")),
            market_cap=_dec(info.get("marketCap")),
            pe=_dec(info.get("trailingPE")),
            pb=_dec(info.get("priceToBook")),
            roe_pct=_dec(info.get("returnOnEquity"), HUNDRED),
            debt_to_equity=_dec(info.get("debtToEquity")),
            profit_margin_pct=_dec(info.get("profitMargins"), HUNDRED),
            revenue_growth_pct=_dec(info.get("revenueGrowth"), HUNDRED),
            # yfinance now returns dividendYield already as a percent; pass it through.
            dividend_yield_pct=_dec(info.get("dividendYield")),
            beta=_dec(info.get("beta")),
            week52_high=_dec(info.get("fiftyTwoWeekHigh")),
            week52_low=_dec(info.get("fiftyTwoWeekLow")),
        )
