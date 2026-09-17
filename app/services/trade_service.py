"""Recording one trade at a time -- the hand-entered counterpart to the file upload.

A trade is one priced BUY or SELL row in the same append-only ledger the upload writes,
so everything downstream (FIFO, realised P&L, the dashboard, the agents) sees it exactly
as it sees an uploaded holding. The "recent trades" feed reads that ledger back, newest
first, and tells the user what each trade did to the ticker's allocation.

Unlike the file-based remove (which writes a price-0 SELL because a deletion sheet has
no price column), a recorded sell carries its price -- which is what lets the ledger
compute a realised gain for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.engine import Connection

from app.core.calculations import (
    InsufficientUnitsError,
    q_money,
    q_pct,
    q_units,
    replay_ledger,
)
from app.core.models import Instrument, Position, Transaction, TxnType
from app.core.sectors import UNCLASSIFIED, Market, resolve_sector
from app.market.fundamentals import default_provider
from app.store import repository

log = logging.getLogger(__name__)

ZERO = Decimal(0)
HUNDRED = Decimal(100)
SOURCE = "manual"


class TradeError(ValueError):
    """A trade the ledger won't accept. The message is written for the user."""


@dataclass
class TradeResult:
    ticker: str
    side: str
    units: Decimal
    price: Decimal
    txn_date: date
    allocation_pct: Decimal  # this ticker's share of invested, right after the trade
    units_held: Decimal  # after the trade
    instrument_created: bool = False


def record_trade(
    conn: Connection,
    user_id: str,
    market: Market,
    *,
    ticker: str,
    side: str,
    units: Decimal,
    price: Decimal,
    txn_date: date,
    name: str | None = None,
    sector: str | None = None,
) -> TradeResult:
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise TradeError("Enter a ticker.")
    try:
        side_t = TxnType((side or "").strip().upper())
    except ValueError:
        raise TradeError("Side must be BUY or SELL.") from None
    if units is None or units <= ZERO:
        raise TradeError("Quantity must be greater than zero.")
    if price is None or price <= ZERO:
        raise TradeError("Price per share must be greater than zero.")
    if txn_date > date.today():
        raise TradeError("The trade date can't be in the future.")

    transactions = repository.get_transactions(conn, user_id, market)
    held = replay_ledger(transactions).positions.get(ticker)
    held_units = held.units if held else ZERO

    # The row as the ledger will see it. A seq above every existing one mirrors the id
    # the database is about to assign, so a same-day sell sorts after that day's buys.
    probe = Transaction(
        ticker=ticker,
        market=market,
        txn_type=side_t,
        units=units,
        price_per_unit=price,
        txn_date=txn_date,
        seq=max((t.seq for t in transactions), default=0) + 1,
    )

    created = False
    if side_t is TxnType.SELL:
        if units > held_units:
            raise TradeError(
                f"Cannot sell {q_units(units)} units of {ticker}: only {q_units(held_units)} held."
            )
        # A backdated sell can pass the check above yet land before the buys it needs.
        # Replay with it included so the ledger is never left in a state that won't build.
        try:
            replay_ledger([*transactions, probe])
        except InsufficientUnitsError as exc:
            raise TradeError(
                f"On {txn_date.isoformat()} you did not yet hold {q_units(units)} units of "
                f"{ticker} ({exc.held} held then). Check the date."
            ) from None
    else:
        instruments = repository.get_instruments(conn, user_id, market)
        if ticker not in instruments:
            repository.upsert_instruments(
                conn, user_id, [_new_instrument(market, ticker, name, sector)]
            )
            created = True

    repository.add_transactions(conn, user_id, [probe], source_file=SOURCE)

    after = replay_ledger(repository.get_transactions(conn, user_id, market)).positions
    position = after.get(ticker)
    return TradeResult(
        ticker=ticker,
        side=side_t.value,
        units=q_units(units),
        price=q_money(price),
        txn_date=txn_date,
        allocation_pct=_allocation(after, ticker),
        units_held=q_units(position.units) if position else ZERO,
        instrument_created=created,
    )


def recent_trades(
    conn: Connection, user_id: str, market: Market, limit: int = 10
) -> list[dict]:
    """The last `limit` ledger rows, newest first, each with the ticker's allocation right
    after it and -- for a priced sell -- the gain it realised. A price-0 sell (the file
    remove) is reported as "removed": it changed the holding, but its outcome is unknown."""
    transactions = repository.get_transactions(conn, user_id, market)  # date, id ascending
    if not transactions:
        return []
    instruments = repository.get_instruments(conn, user_id, market)
    gains = {s.seq: s.gain for s in replay_ledger(transactions).realised}

    out: list[dict] = []
    start = max(0, len(transactions) - limit)
    for i in range(len(transactions) - 1, start - 1, -1):
        txn = transactions[i]
        # Replay only up to this row: the allocation *as of* the trade, not today's.
        positions = replay_ledger(transactions[: i + 1]).positions
        instrument = instruments.get(txn.ticker)
        priced = txn.price_per_unit > ZERO
        if txn.txn_type is TxnType.BUY:
            kind = "buy"
        else:
            kind = "sell" if priced else "removed"
        out.append(
            {
                "id": txn.seq,
                "ticker": txn.ticker,
                "name": instrument.name if instrument else txn.ticker,
                "kind": kind,
                "units": str(q_units(txn.units)),
                "price": str(q_money(txn.price_per_unit)) if priced else None,
                "date": txn.txn_date.isoformat(),
                "allocation_pct": str(_allocation(positions, txn.ticker)),
                "realised_gain": str(q_money(gains[txn.seq])) if txn.seq in gains else None,
            }
        )
    return out


def _new_instrument(
    market: Market, ticker: str, name: str | None, sector: str | None
) -> Instrument:
    """A ticker we haven't seen. Take the name and sector the user gave; fill any gap
    best-effort from the fundamentals provider; and file an unrecognised sector under
    Others -- the same rule the file uploader applies, so a hand-entered holding and an
    uploaded one are classified identically."""
    name = (name or "").strip()
    resolved = resolve_sector(market, sector) if sector else None

    if not name or resolved is None:
        try:
            fundamentals = default_provider().get_fundamentals(market, ticker)
        except Exception:  # noqa: BLE001 -- a lookup miss must never block the trade
            fundamentals = None
        if fundamentals is not None:
            name = name or (fundamentals.name or "")
            if resolved is None and fundamentals.sector:
                resolved = resolve_sector(market, fundamentals.sector)

    return Instrument(ticker, market, (name or ticker)[:160], resolved or UNCLASSIFIED)


def _allocation(positions: dict[str, Position], ticker: str) -> Decimal:
    total = sum((p.invested for p in positions.values()), ZERO)
    position = positions.get(ticker)
    if position is None or total == ZERO:
        return ZERO
    return q_pct(position.invested / total * HUNDRED)
