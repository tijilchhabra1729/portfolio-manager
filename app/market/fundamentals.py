"""Fundamental ratios for one ticker, from yfinance `.info`, with a price-endpoint fallback.

Feeds the Explore tab: valuation (P/E, P/B), profitability (ROE, margin), leverage
(debt/equity), growth (revenue), income (dividend yield), plus name/sector/market cap and
the 52-week range for a momentum read. Everything is optional — `.info` omits fields for
many symbols — so each is `None`-tolerant and the caller shows a dash rather than failing.

Yahoo blocks the `.info` (quoteSummary) endpoint from many datacenter IPs, which is why
Explore works on a laptop but returns "No data" on a cloud host. When `.info` comes back
empty we degrade to the price endpoint (`fast_info` / history) — which Yahoo does NOT block,
so the dashboard's prices work there too — and return the basics (name/price/market cap/
52-week) with the ratios blank, rather than nothing at all.

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


def _fast(fi: Any, name: str) -> Any:
    """Read a FastInfo attribute without letting one missing field abort the fallback."""
    try:
        return getattr(fi, name)
    except Exception:
        return None


class YFinanceFundamentals:
    def get_fundamentals(self, market: Market, ticker: str) -> Fundamentals | None:
        tk = yf.Ticker(yf_symbol(ticker, market))
        try:
            info = tk.info or {}
        except Exception:
            # The .info (quoteSummary) endpoint is blocked from many datacenter IPs — the
            # exact reason Explore works locally but not on a cloud host. Don't give up here;
            # fall through to the price endpoint, which is not blocked.
            log.info("fundamentals .info unavailable for %s; using the price endpoint", ticker)
            info = {}

        if info.get("longName") or info.get("shortName") or info.get("trailingPE") or info.get("regularMarketPrice"):
            return self._from_info(ticker, info)

        # .info was empty/blocked: show the basics from the (unblocked) price endpoint rather
        # than a bare "No data". Ratios stay None -> the UI renders a dash.
        return self._from_price(ticker, tk, info)

    def _from_info(self, ticker: str, info: dict) -> Fundamentals:
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

    def _from_price(self, ticker: str, tk: "yf.Ticker", info: dict) -> Fundamentals | None:
        price = market_cap = high = low = None
        currency = info.get("currency")
        # fast_info uses the lightweight price/quote endpoints, not the blocked quoteSummary.
        try:
            fi = tk.fast_info
            price = _dec(_fast(fi, "last_price"))
            market_cap = _dec(_fast(fi, "market_cap"))
            high = _dec(_fast(fi, "year_high"))
            low = _dec(_fast(fi, "year_low"))
            currency = currency or _fast(fi, "currency")
        except Exception:
            log.debug("fast_info failed for %s", ticker, exc_info=True)
        # Last resort: derive price and the 52-week range from history — the exact endpoint
        # the dashboard's prices use, so it works wherever those do.
        if price is None or high is None or low is None:
            try:
                h = tk.history(period="1y")
                if not h.empty:
                    price = price if price is not None else _dec(h["Close"].iloc[-1])
                    high = high if high is not None else _dec(h["High"].max())
                    low = low if low is not None else _dec(h["Low"].min())
            except Exception:
                log.debug("history fallback failed for %s", ticker, exc_info=True)

        if price is None and market_cap is None:
            return None  # genuinely nothing (a bad symbol): keep the honest "No data"
        return Fundamentals(
            ticker=ticker.upper(),
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            currency=currency,
            price=price,
            market_cap=market_cap,
            pe=None, pb=None, roe_pct=None, debt_to_equity=None,
            profit_margin_pct=None, revenue_growth_pct=None, dividend_yield_pct=None, beta=None,
            week52_high=high, week52_low=low,
        )
