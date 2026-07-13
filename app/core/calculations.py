"""Portfolio maths. Pure functions, Decimal in and Decimal out, no I/O.

This module is deliberately free of database and network dependencies. The agent layer
will need to run exactly this maths over a *hypothetical* portfolio -- "what happens to
my sector balance if I exit this position?" -- and that is only possible if the maths is
callable on data that does not exist in the database.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping

from app.core.models import (
    MONEY,
    PERCENT,
    UNITS,
    DashboardView,
    Instrument,
    Lot,
    Position,
    Quote,
    SectorRow,
    StockRow,
    Totals,
    Transaction,
    TxnType,
)
from app.core.sectors import Market, spec

ZERO = Decimal(0)
HUNDRED = Decimal(100)


class InsufficientUnitsError(ValueError):
    """A delete asked for more units than the portfolio holds."""

    def __init__(self, ticker: str, requested: Decimal, held: Decimal) -> None:
        self.ticker = ticker
        self.requested = requested
        self.held = held
        super().__init__(
            f"Cannot remove {requested} units of {ticker}: only {held} held."
        )


def q_money(x: Decimal) -> Decimal:
    return x.quantize(MONEY, rounding=ROUND_HALF_UP)


def q_pct(x: Decimal) -> Decimal:
    return x.quantize(PERCENT, rounding=ROUND_HALF_UP)


def q_units(x: Decimal) -> Decimal:
    """Quantize units, then drop noise zeros -- 100.000000 reads as 100, 10.5 as 10.5."""
    q = x.quantize(UNITS, rounding=ROUND_HALF_UP)
    return q.to_integral_value() if q == q.to_integral_value() else q.normalize()


def build_positions(transactions: Iterable[Transaction]) -> dict[str, Position]:
    """Replay the ledger into current holdings, consuming lots FIFO on a sell.

    A stock bought across several lots at different prices has no single cost basis, so
    the order a sell consumes them in changes the invested amount that remains. We take
    the oldest lot first, which is what both Indian and US tax treatment assume.
    """
    by_ticker: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        by_ticker[txn.ticker].append(txn)

    positions: dict[str, Position] = {}
    for ticker, txns in by_ticker.items():
        # Sells carry the date of the delete, so they naturally sort after the buys they
        # consume. seq breaks same-day ties in ledger insertion order.
        txns.sort(key=lambda t: (t.txn_date, t.seq))

        lots: deque[Lot] = deque()
        for txn in txns:
            if txn.txn_type == TxnType.BUY:
                lots.append(Lot(txn.units, txn.price_per_unit, txn.txn_date))
                continue

            outstanding = txn.units
            while outstanding > ZERO:
                if not lots:
                    held = sum((lot.units for lot in lots), ZERO)
                    raise InsufficientUnitsError(ticker, txn.units, held)
                oldest = lots[0]
                if oldest.units <= outstanding:
                    outstanding -= oldest.units
                    lots.popleft()
                else:
                    lots[0] = Lot(
                        oldest.units - outstanding,
                        oldest.price_per_unit,
                        oldest.purchase_date,
                    )
                    outstanding = ZERO

        units = sum((lot.units for lot in lots), ZERO)
        if units <= ZERO:
            continue  # fully exited; the doc says the stock leaves the portfolio
        positions[ticker] = Position(
            ticker=ticker,
            units=units,
            invested=sum((lot.cost for lot in lots), ZERO),
            lots=tuple(lots),
        )
    return positions


def held_units(transactions: Iterable[Transaction], ticker: str) -> Decimal:
    position = build_positions(transactions).get(ticker)
    return position.units if position else ZERO


def build_dashboard(
    market: Market,
    positions: Mapping[str, Position],
    instruments: Mapping[str, Instrument],
    quotes: Mapping[str, Quote],
    now: datetime | None = None,
) -> DashboardView:
    """Assemble the two tables the doc specifies, plus totals.

    Allocation % is computed on invested amount, never market value -- the doc says so
    for both tables. A useful consequence: allocation stays correct even when every
    price fetch fails, because it never touches a price at all.
    """
    now = now or datetime.now(UTC)
    market_spec = spec(market)
    total_invested = sum((p.invested for p in positions.values()), ZERO)

    def allocation_of(invested: Decimal) -> Decimal:
        if total_invested == ZERO:
            return ZERO
        return q_pct(invested / total_invested * HUNDRED)

    stocks: list[StockRow] = []
    unpriced: list[str] = []

    for ticker, position in positions.items():
        instrument = instruments.get(ticker)
        name = instrument.name if instrument else ticker
        sector = instrument.sector if instrument else "Other"
        quote = quotes.get(ticker)

        price = market_value = pnl = pnl_pct = None
        if quote is not None:
            price = quote.price
            market_value = q_money(position.units * quote.price)
            pnl = q_money(market_value - position.invested)
            if position.invested != ZERO:
                pnl_pct = q_pct(pnl / position.invested * HUNDRED)
        else:
            unpriced.append(ticker)

        stocks.append(
            StockRow(
                sno=0,  # assigned after sorting
                ticker=ticker,
                name=name,
                sector=sector,
                units=q_units(position.units),
                invested=q_money(position.invested),
                allocation_pct=allocation_of(position.invested),
                price=price,
                market_value=market_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                stale_price=bool(quote and quote.stale),
            )
        )

    # Largest allocation first: concentration is the thing the user is looking for.
    stocks.sort(key=lambda r: (-r.allocation_pct, r.ticker))
    stocks = [
        StockRow(**{**row.__dict__, "sno": i}) for i, row in enumerate(stocks, start=1)
    ]

    sectors = _build_sector_rows(stocks, allocation_of)

    priced = [r for r in stocks if r.market_value is not None]
    total_mv = sum((r.market_value for r in priced), ZERO) if priced else None
    priced_invested = sum((r.invested for r in priced), ZERO)
    total_pnl = q_money(total_mv - priced_invested) if total_mv is not None else None
    total_pnl_pct = (
        q_pct(total_pnl / priced_invested * HUNDRED)
        if total_pnl is not None and priced_invested != ZERO
        else None
    )

    return DashboardView(
        market=market,
        currency=market_spec.currency,
        symbol=market_spec.symbol,
        stocks=tuple(stocks),
        sectors=tuple(sectors),
        totals=Totals(
            invested=q_money(total_invested),
            market_value=q_money(total_mv) if total_mv is not None else None,
            pnl=total_pnl,
            pnl_pct=total_pnl_pct,
            stock_count=len(stocks),
            sector_count=len(sectors),
        ),
        generated_at=now,
        unpriced=tuple(sorted(unpriced)),
    )


def _build_sector_rows(stocks: list[StockRow], allocation_of) -> list[SectorRow]:
    grouped: dict[str, list[StockRow]] = defaultdict(list)
    for row in stocks:
        grouped[row.sector].append(row)

    rows: list[SectorRow] = []
    for sector, members in grouped.items():
        invested = sum((m.invested for m in members), ZERO)
        priced = [m for m in members if m.market_value is not None]
        unpriced_count = len(members) - len(priced)

        market_value = pnl = pnl_pct = None
        if priced:
            # Aggregates cover the priced holdings only. unpriced_count tells the reader
            # the figure is partial rather than leaving them to assume it is complete.
            market_value = q_money(sum((m.market_value for m in priced), ZERO))
            priced_invested = sum((m.invested for m in priced), ZERO)
            pnl = q_money(market_value - priced_invested)
            if priced_invested != ZERO:
                pnl_pct = q_pct(pnl / priced_invested * HUNDRED)

        rows.append(
            SectorRow(
                sno=0,
                sector=sector,
                stock_count=len(members),
                invested=q_money(invested),
                allocation_pct=allocation_of(invested),
                market_value=market_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                unpriced_count=unpriced_count,
            )
        )

    rows.sort(key=lambda r: (-r.allocation_pct, r.sector))
    return [SectorRow(**{**row.__dict__, "sno": i}) for i, row in enumerate(rows, start=1)]
