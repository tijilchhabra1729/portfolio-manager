"""Market definitions and their sector taxonomies.

Sectors are a closed vocabulary per market: the intake spreadsheet offers them as a
dropdown and the validator rejects anything else. That keeps sector allocation
deterministic rather than at the mercy of whatever a data provider happens to report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    INDIA = "INDIA"
    US = "US"


# NSE-oriented grouping. Banking and Financial Services are kept apart because a
# portfolio can be badly concentrated in banks while looking balanced under a single
# merged "Financials" bucket.
INDIA_SECTORS: tuple[str, ...] = (
    "Auto",
    "Banking",
    "Financial Services",
    "IT",
    "Pharma & Healthcare",
    "FMCG",
    "Metals & Mining",
    "Oil & Gas / Energy",
    "Chemicals",
    "Cement & Construction",
    "Capital Goods / Industrials",
    "Power & Utilities",
    "Telecom",
    "Realty",
    "Media & Entertainment",
    "Consumer Durables",
    "Infrastructure",
    "Textiles",
    "Agri",
    "Defence",
    "Diversified",
    "Other",
)

# The 11 GICS sectors, plus a catch-all for ETFs and anything unclassifiable.
US_SECTORS: tuple[str, ...] = (
    "Information Technology",
    "Health Care",
    "Financials",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Real Estate",
    "Materials",
    "Other",
)


@dataclass(frozen=True)
class MarketSpec:
    code: Market
    label: str
    currency: str
    symbol: str
    yf_suffix: str
    sectors: tuple[str, ...]


MARKETS: dict[Market, MarketSpec] = {
    Market.INDIA: MarketSpec(
        code=Market.INDIA,
        label="India (NSE)",
        currency="INR",
        symbol="₹",
        yf_suffix=".NS",
        sectors=INDIA_SECTORS,
    ),
    Market.US: MarketSpec(
        code=Market.US,
        label="United States",
        currency="USD",
        symbol="$",
        yf_suffix="",
        sectors=US_SECTORS,
    ),
}


def spec(market: Market) -> MarketSpec:
    return MARKETS[market]


def is_valid_sector(market: Market, sector: str) -> bool:
    return sector in MARKETS[market].sectors


def yf_symbol(ticker: str, market: Market) -> str:
    """Map a plain ticker to the symbol yfinance expects (RELIANCE -> RELIANCE.NS)."""
    ticker = ticker.strip().upper()
    suffix = MARKETS[market].yf_suffix
    if suffix and not ticker.endswith(suffix):
        return f"{ticker}{suffix}"
    return ticker
