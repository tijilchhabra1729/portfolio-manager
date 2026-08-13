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

import json
import logging
import urllib.parse
import urllib.request
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


def _usable_info(info: dict) -> bool:
    return bool(
        info.get("longName") or info.get("shortName")
        or info.get("trailingPE") or info.get("regularMarketPrice")
    )


def _has_ratios(f: Fundamentals) -> bool:
    """True if the read carries real valuation/quality ratios (not just price + 52-week)."""
    return any(v is not None for v in (f.pe, f.pb, f.roe_pct, f.profit_margin_pct, f.debt_to_equity))


class YFinanceFundamentals:
    def __init__(self, keyed: FundamentalsProvider | None = None) -> None:
        # A keyed source (Finnhub) consulted when Yahoo's `.info` returns no ratios — the
        # usual case on a cloud host — before degrading to the price-only read.
        self.keyed = keyed

    def get_fundamentals(self, market: Market, ticker: str) -> Fundamentals | None:
        tk = yf.Ticker(yf_symbol(ticker, market))
        try:
            info = tk.info or {}
        except Exception:
            # The .info (quoteSummary) endpoint is blocked from many datacenter IPs — the
            # exact reason Explore works locally but not on a cloud host.
            log.info("fundamentals .info unavailable for %s; trying fallbacks", ticker)
            info = {}

        primary = self._from_info(ticker, info) if _usable_info(info) else None
        if primary is not None and _has_ratios(primary):
            return primary  # full read — works locally, and for names Yahoo doesn't block

        # .info gave no ratios (blocked on this IP, or a thin symbol). Try a keyed API that
        # works from datacenter IPs for the real ratios...
        if self.keyed is not None:
            keyed = self.keyed.get_fundamentals(market, ticker)
            if keyed is not None and _has_ratios(keyed):
                return keyed

        # ...otherwise return whatever .info gave, else the price endpoint (price/mktcap/52wk,
        # ratios blank) so a cloud host still shows numbers rather than a bare "No data".
        return primary or self._from_price(ticker, tk, info)

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


def _first(metric: dict, *keys: str) -> Any:
    """The first present, non-null value among the given metric keys (their names drift a
    little across Finnhub's dataset, so we try a few)."""
    for key in keys:
        value = metric.get(key)
        if value is not None:
            return value
    return None


class FinnhubFundamentals:
    """US fundamentals from Finnhub's free tier — a keyed source that works from datacenter
    IPs, so Explore shows ratios on a cloud host where Yahoo's `.info` is blocked. The free
    plan carries US-listed equities; NSE/BSE symbols aren't on it, so those return None and
    the caller degrades to the price endpoint. Three lightweight GETs per lookup (profile,
    metrics, quote); well inside the free 60-calls/minute allowance for Explore's usage.

    Units are normalised to match the yfinance path so a number reads the same whether it
    came from the laptop or the host: ROE/margin/growth/yield arrive from Finnhub already as
    percentages (passed through), market cap arrives in millions (scaled up), and the debt/
    equity ratio is scaled ×100 to match yfinance's convention.
    """

    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, *, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def get_fundamentals(self, market: Market, ticker: str) -> Fundamentals | None:
        # Free tier is US-listed symbols, keyed by the plain ticker (no exchange suffix).
        if not self.api_key or market != Market.US:
            return None
        sym = ticker.upper()

        profile = self._get("/stock/profile2", {"symbol": sym}) or {}
        metric = (self._get("/stock/metric", {"symbol": sym, "metric": "all"}) or {}).get("metric") or {}
        quote = self._get("/quote", {"symbol": sym}) or {}
        # A valid-but-unknown symbol comes back as an empty profile with no metrics.
        if not profile.get("name") and not metric:
            return None

        HUNDRED = Decimal(100)
        return Fundamentals(
            ticker=sym,
            name=profile.get("name"),
            sector=profile.get("finnhubIndustry"),
            currency=profile.get("currency"),
            price=_dec(quote.get("c")),
            market_cap=_dec(profile.get("marketCapitalization"), Decimal(1_000_000)),  # millions -> absolute
            pe=_dec(_first(metric, "peTTM", "peBasicExclExtraTTM", "peNormalizedAnnual", "peAnnual")),
            pb=_dec(_first(metric, "pbAnnual", "pbQuarterly", "pbTTM")),
            roe_pct=_dec(_first(metric, "roeTTM", "roeRfy", "roeAnnual")),  # already a percentage
            debt_to_equity=_dec(
                _first(metric, "totalDebt/totalEquityAnnual", "totalDebt/totalEquityQuarterly", "longTermDebt/equityAnnual"),
                HUNDRED,  # ratio -> match yfinance's ×100 convention
            ),
            profit_margin_pct=_dec(_first(metric, "netProfitMarginTTM", "netProfitMarginAnnual")),  # already %
            revenue_growth_pct=_dec(_first(metric, "revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy", "revenueGrowth5Y")),  # already %
            dividend_yield_pct=_dec(_first(metric, "currentDividendYieldTTM", "dividendYieldIndicatedAnnual")),  # already %
            beta=_dec(metric.get("beta")),
            week52_high=_dec(_first(metric, "52WeekHigh")),
            week52_low=_dec(_first(metric, "52WeekLow")),
        )

    def _get(self, path: str, params: dict) -> dict | None:
        query = urllib.parse.urlencode({**params, "token": self.api_key})
        url = f"{self.BASE}{path}?{query}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "portfolio-manager"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except Exception:
            log.debug("finnhub %s failed for %s", path, params.get("symbol"), exc_info=True)
            return None


def default_provider() -> YFinanceFundamentals:
    """The provider the app runs: yfinance, plus Finnhub as a keyed fallback for US ratios
    when FINNHUB_API_KEY is set — so a cloud host isn't limited to yfinance's blocked `.info`.
    Locally, `.info` works and Finnhub is never called (no key needed)."""
    from app.config import settings  # local import: config must not import this module

    key = settings().finnhub_api_key
    return YFinanceFundamentals(keyed=FinnhubFundamentals(key) if key else None)
