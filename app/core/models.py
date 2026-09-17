"""Domain models.

Everything monetary is a Decimal. Floats are never allowed to touch money: 0.1 has no
exact binary representation, and the error compounds through
units x price -> invested -> allocation %.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.core.sectors import Market

MONEY = Decimal("0.01")
UNITS = Decimal("0.000001")
PERCENT = Decimal("0.01")


class TxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Instrument:
    ticker: str
    market: Market
    name: str
    sector: str


@dataclass(frozen=True)
class Transaction:
    """One line of the append-only ledger. Positions are derived from these, never
    stored directly, so the portfolio always has a full audit trail."""

    ticker: str
    market: Market
    txn_type: TxnType
    units: Decimal
    price_per_unit: Decimal
    txn_date: date
    seq: int = 0  # tiebreak for same-day transactions; the store supplies the row id


@dataclass(frozen=True)
class Lot:
    """An unconsumed slice of a purchase."""

    units: Decimal
    price_per_unit: Decimal
    purchase_date: date

    @property
    def cost(self) -> Decimal:
        return self.units * self.price_per_unit


@dataclass(frozen=True)
class Position:
    """What remains of a holding after FIFO sells have been applied."""

    ticker: str
    units: Decimal
    invested: Decimal  # cost basis of the *remaining* units only
    lots: tuple[Lot, ...] = field(default_factory=tuple)

    @property
    def avg_cost(self) -> Decimal:
        if self.units == 0:
            return Decimal(0)
        return self.invested / self.units


@dataclass(frozen=True)
class RealisedSale:
    """One priced sell, with the gain it locked in: proceeds minus the FIFO cost basis of
    the lots it consumed. Only sells that carry a price produce one -- a price-0 "remove"
    has an unknown outcome and is honestly excluded rather than counted as zero gain."""

    ticker: str
    txn_date: date
    seq: int
    units: Decimal
    sell_price: Decimal
    cost_basis: Decimal
    gain: Decimal


@dataclass(frozen=True)
class LedgerReplay:
    """Everything one FIFO pass over the ledger tells us: what is still held, and what
    each priced sell realised on the way."""

    positions: dict[str, Position]
    realised: tuple[RealisedSale, ...] = field(default_factory=tuple)

    @property
    def realised_pnl(self) -> Decimal:
        return sum((s.gain for s in self.realised), Decimal(0))


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: Decimal
    as_of: datetime
    market_cap: Decimal | None = None
    stale: bool = False  # served from cache past its TTL because a live fetch failed


@dataclass(frozen=True)
class StockRow:
    """A row of the doc's stock-allocation table."""

    sno: int
    ticker: str
    name: str
    sector: str
    units: Decimal
    invested: Decimal
    allocation_pct: Decimal  # cost basis; never depends on price
    price: Decimal | None = None
    market_value: Decimal | None = None
    pnl: Decimal | None = None
    pnl_pct: Decimal | None = None
    stale_price: bool = False
    # Company size, so "is this a small cap" is answerable at a glance rather than only
    # when an agent warns. None when the provider gave us no market cap.
    market_cap: Decimal | None = None
    cap_class: str | None = None  # "large" | "mid" | "small"
    # Average buy price of the remaining units (cost basis / units) and the day's move
    # against the previous close. Both None-tolerant: no prior close means no day change.
    avg_cost: Decimal | None = None
    day_change_pct: Decimal | None = None


@dataclass(frozen=True)
class SectorRow:
    """A row of the doc's sector-allocation table."""

    sno: int
    sector: str
    stock_count: int
    invested: Decimal
    allocation_pct: Decimal
    market_value: Decimal | None = None
    pnl: Decimal | None = None
    pnl_pct: Decimal | None = None
    # How many holdings in this sector had no price. Non-zero means the market value
    # above covers only part of the sector and is understated.
    unpriced_count: int = 0


@dataclass(frozen=True)
class Totals:
    invested: Decimal
    market_value: Decimal | None
    pnl: Decimal | None  # unrealised: on the positions still held
    pnl_pct: Decimal | None
    stock_count: int
    sector_count: int
    # The fuller P&L picture. Every percentage is relative to `invested`, and net P&L is
    # realised + unrealised + options income. All defaulted, so a view built without them
    # (tests, the exporter) is unchanged.
    realised_pnl: Decimal = Decimal(0)
    realised_pnl_pct: Decimal | None = None
    options_income: Decimal = Decimal(0)
    options_income_pct: Decimal | None = None
    net_pnl: Decimal | None = None
    net_pnl_pct: Decimal | None = None
    # Cash position: what the user set aside to invest, less what is invested. cash_pct
    # is cash / investable (the share of the pot still uninvested; negative = over-invested).
    investable: Decimal | None = None
    cash: Decimal | None = None
    cash_pct: Decimal | None = None


@dataclass(frozen=True)
class DashboardView:
    market: Market
    currency: str
    symbol: str
    stocks: tuple[StockRow, ...]
    sectors: tuple[SectorRow, ...]
    totals: Totals
    generated_at: datetime
    # Tickers we hold but could not price. Surfaced rather than silently treated as
    # zero: a blank market value is honest, a fabricated one is not.
    unpriced: tuple[str, ...] = field(default_factory=tuple)
