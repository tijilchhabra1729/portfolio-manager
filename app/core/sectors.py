"""Market definitions and their sector taxonomies.

The India list is Zerodha's (https://zerodha.com/markets/sector/), verbatim. Matching a
broker's own vocabulary means their export usually lands without an edit -- and it means
"Financial services" covers banks, because that is how Zerodha files them.

The taxonomy is CLOSED. Anything unrecognised becomes "Others" and is reported, never
invented. A free-form sector list quietly destroys the only number this product exists to
compute: upload "Bankng" once and you get "Financial services 12%" and "Bankng 8%" as two
separate industries, with nothing anywhere telling you they are the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# The bucket for anything we cannot place. Reported on upload -- never applied silently.
UNCLASSIFIED = "Others"


class Market(str, Enum):
    INDIA = "INDIA"
    US = "US"


# Zerodha's sector list, exactly as they publish it, plus our fallback bucket. Note there
# is no "Banking": banks sit under Financial services, and NBFC is broken out separately.
INDIA_SECTORS: tuple[str, ...] = (
    "Agriculture",
    "Auto ancillary",
    "Automobile",
    "Aviation",
    "Building materials",
    "Chemicals",
    "Consumer durables",
    "Dairy products",
    "Defence",
    "Diversified",
    "Education & training",
    "Energy",
    "Engineering & capital goods",
    "FMCG",
    "Fertilizers",
    "Financial services",
    "Healthcare",
    "IT",
    "Logistics",
    "Media & entertainment",
    "Metals",
    "Miscellaneous",
    "NBFC",
    "Packaging",
    "Plastic pipes",
    "Real estate",
    "Retail",
    "Services",
    "Silver",
    "Software services",
    "Solar panel",
    "Telecom",
    "Textiles",
    "Tourism & hospitality",
    "Trading",
    UNCLASSIFIED,
)

# The 11 GICS sectors, plus the same fallback.
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
    UNCLASSIFIED,
)

# Brokers and data vendors do not all use the same words. Aliases are matched after
# lowercasing and stripping punctuation, so "Oil & Gas", "oil and gas" and "OIL/GAS" all
# resolve identically. Per-market, because "Financial services" and "Financials" are the
# same idea spelled for different taxonomies.
INDIA_ALIASES: dict[str, str] = {
    "bank": "Financial services",
    "banks": "Financial services",
    "banking": "Financial services",
    "private sector bank": "Financial services",
    "public sector bank": "Financial services",
    "psu bank": "Financial services",
    "finance": "Financial services",
    "financials": "Financial services",
    "insurance": "Financial services",
    "capital markets": "Financial services",
    "non banking financial company": "NBFC",
    "information technology": "IT",
    "it services": "IT",
    "tech": "IT",
    "technology": "IT",
    "software": "Software services",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    "pharmaceuticals": "Healthcare",
    "health care": "Healthcare",
    "hospital": "Healthcare",
    "hospitals": "Healthcare",
    "auto": "Automobile",
    "automobiles": "Automobile",
    "auto ancillaries": "Auto ancillary",
    "auto components": "Auto ancillary",
    "oil and gas": "Energy",
    "oil gas": "Energy",
    "petroleum": "Energy",
    "refineries": "Energy",
    "power": "Energy",
    "utilities": "Energy",
    "electricity": "Energy",
    "coal": "Energy",
    "mining": "Metals",
    "steel": "Metals",
    "metals and mining": "Metals",
    "cement": "Building materials",
    "construction": "Building materials",
    "infrastructure": "Building materials",
    "realty": "Real estate",
    "capital goods": "Engineering & capital goods",
    "industrials": "Engineering & capital goods",
    "engineering": "Engineering & capital goods",
    "telecommunication": "Telecom",
    "telecommunications": "Telecom",
    "media": "Media & entertainment",
    "entertainment": "Media & entertainment",
    "consumer goods": "FMCG",
    "fast moving consumer goods": "FMCG",
    "agri": "Agriculture",
    "agro": "Agriculture",
    "defense": "Defence",
    "conglomerate": "Diversified",
    "airlines": "Aviation",
    "hotels": "Tourism & hospitality",
    "hospitality": "Tourism & hospitality",
    "shipping": "Logistics",
    "misc": "Miscellaneous",
    "other": UNCLASSIFIED,
}

US_ALIASES: dict[str, str] = {
    "technology": "Information Technology",
    "tech": "Information Technology",
    "it": "Information Technology",
    "software": "Information Technology",
    "healthcare": "Health Care",
    "financial services": "Financials",
    "finance": "Financials",
    "banking": "Financials",
    "banks": "Financials",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "communications": "Communication Services",
    "telecom": "Communication Services",
    "basic materials": "Materials",
    "realty": "Real Estate",
    "oil and gas": "Energy",
    "other": UNCLASSIFIED,
}


@dataclass(frozen=True)
class MarketSpec:
    code: Market
    label: str
    currency: str
    symbol: str
    # Symbols to try on the data provider, in order. A stock can be listed on one Indian
    # exchange and not the other, so a .NS miss is worth retrying on .BO.
    #
    # These stay WITHIN one market on purpose. Ticker symbols are not globally unique --
    # IEX is Indian Energy Exchange on the NSE and an energy company on the NASDAQ -- so
    # falling back from a US symbol to a .NS one would price a US holding with a rupee
    # quote and label it in dollars. The currency guard in YFinanceProvider is the second
    # line of defence against exactly that.
    yf_suffixes: tuple[str, ...]
    sectors: tuple[str, ...]
    aliases: dict[str, str]

    @property
    def yf_suffix(self) -> str:
        return self.yf_suffixes[0]


MARKETS: dict[Market, MarketSpec] = {
    Market.INDIA: MarketSpec(
        code=Market.INDIA,
        label="India (NSE)",
        currency="INR",
        symbol="₹",
        yf_suffixes=(".NS", ".BO"),  # NSE first, then BSE for anything NSE does not list
        sectors=INDIA_SECTORS,
        aliases=INDIA_ALIASES,
    ),
    Market.US: MarketSpec(
        code=Market.US,
        label="United States",
        currency="USD",
        symbol="$",
        yf_suffixes=("",),
        sectors=US_SECTORS,
        aliases=US_ALIASES,
    ),
}


def spec(market: Market) -> MarketSpec:
    return MARKETS[market]


def _normalise(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in value.lower())
    return " ".join(cleaned.split())


def resolve_sector(market: Market, raw: str) -> str | None:
    """Map whatever the file says onto this market's taxonomy, or None if we can't.

    Exact match first, then an alias. We never guess beyond that: the caller decides what
    an unrecognised sector becomes, and says so out loud.
    """
    raw = raw.strip()
    if not raw:
        return None
    market_spec = MARKETS[market]

    normalised = _normalise(raw)
    for sector in market_spec.sectors:
        if _normalise(sector) == normalised:
            return sector

    alias = market_spec.aliases.get(normalised)
    return alias if alias in market_spec.sectors else None


def suggest_sectors(market: Market, raw: str, limit: int = 3) -> list[str]:
    from difflib import get_close_matches

    return get_close_matches(raw, MARKETS[market].sectors, n=limit, cutoff=0.5)


def is_valid_sector(market: Market, sector: str) -> bool:
    return sector in MARKETS[market].sectors


def yf_symbol(ticker: str, market: Market, suffix: str | None = None) -> str:
    """Map a plain ticker to the symbol yfinance expects (RELIANCE -> RELIANCE.NS)."""
    ticker = ticker.strip().upper()
    if suffix is None:
        suffix = MARKETS[market].yf_suffix
    if suffix and not ticker.endswith(suffix):
        return f"{ticker}{suffix}"
    return ticker


def yf_candidates(ticker: str, market: Market) -> list[str]:
    """Every symbol worth trying for this ticker, best first."""
    seen: list[str] = []
    for suffix in MARKETS[market].yf_suffixes:
        candidate = yf_symbol(ticker, market, suffix)
        if candidate not in seen:
            seen.append(candidate)
    return seen
