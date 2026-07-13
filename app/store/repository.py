"""Repositories: the only place that knows SQL.

Everything above this layer -- services, the API, and later the agents -- speaks in
domain objects. Each function takes an open Connection so a caller can compose several
writes into one transaction.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from app.core.models import Instrument, Quote, Transaction, TxnType
from app.core.sectors import Market
from app.store.tables import (
    insights,
    instruments,
    portfolio_snapshots,
    price_snapshots,
    transactions,
)

# --- Instruments --------------------------------------------------------------------


def upsert_instruments(
    conn: Connection, user_id: str, records: Iterable[Instrument]
) -> None:
    rows = [
        {
            "user_id": user_id,
            "market": i.market.value,
            "ticker": i.ticker,
            "name": i.name,
            "sector": i.sector,
            "updated_at": datetime.now(),
        }
        for i in records
    ]
    if not rows:
        return
    statement = insert(instruments).values(rows)
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_instrument",
            set_={
                "name": statement.excluded.name,
                "sector": statement.excluded.sector,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )


def get_instruments(
    conn: Connection, user_id: str, market: Market
) -> dict[str, Instrument]:
    rows = conn.execute(
        select(instruments).where(
            instruments.c.user_id == user_id, instruments.c.market == market.value
        )
    ).mappings()
    return {
        r["ticker"]: Instrument(
            ticker=r["ticker"],
            market=Market(r["market"]),
            name=r["name"],
            sector=r["sector"],
        )
        for r in rows
    }


# --- Transactions -------------------------------------------------------------------


def add_transactions(
    conn: Connection, user_id: str, records: Sequence[Transaction], source_file: str
) -> int:
    if not records:
        return 0
    conn.execute(
        transactions.insert(),
        [
            {
                "user_id": user_id,
                "market": t.market.value,
                "ticker": t.ticker,
                "txn_type": t.txn_type.value,
                "units": t.units,
                "price_per_unit": t.price_per_unit,
                "txn_date": t.txn_date,
                "source_file": source_file,
            }
            for t in records
        ],
    )
    return len(records)


def get_transactions(
    conn: Connection, user_id: str, market: Market
) -> list[Transaction]:
    rows = conn.execute(
        select(transactions)
        .where(
            transactions.c.user_id == user_id, transactions.c.market == market.value
        )
        .order_by(transactions.c.txn_date, transactions.c.id)
    ).mappings()
    return [
        Transaction(
            ticker=r["ticker"],
            market=Market(r["market"]),
            txn_type=TxnType(r["txn_type"]),
            units=r["units"],
            price_per_unit=r["price_per_unit"],
            txn_date=r["txn_date"],
            seq=r["id"],  # ledger insertion order breaks same-day ties
        )
        for r in rows
    ]


def get_user_ids(conn: Connection) -> list[str]:
    """Everyone who actually holds something.

    The daily job has no request and therefore no logged-in user, so it cannot ask "whose
    portfolio?" -- it has to snapshot all of them. Hardcoding a single user here would
    quietly record an empty portfolio every night in production, where holdings belong to
    a Supabase UUID rather than the local dev user.
    """
    return [
        r[0]
        for r in conn.execute(
            select(transactions.c.user_id).distinct().order_by(transactions.c.user_id)
        )
    ]


def clear_market(conn: Connection, user_id: str, market: Market) -> None:
    """Wipe one market's ledger and instruments. Backs the doc's bulk upload, which
    replaces the portfolio rather than adding to it."""
    for table in (transactions, instruments):
        conn.execute(
            delete(table).where(
                table.c.user_id == user_id, table.c.market == market.value
            )
        )


# --- Prices -------------------------------------------------------------------------


def upsert_prices(conn: Connection, market: Market, quotes: Iterable[Quote]) -> None:
    """One row per ticker per day: a refresh overwrites today's price, while previous
    days stay put as history."""
    rows = [
        {
            "market": market.value,
            "ticker": q.ticker,
            "price": q.price,
            "market_cap": q.market_cap,
            "captured_on": q.as_of.date(),
            "fetched_at": q.as_of,
        }
        for q in quotes
    ]
    if not rows:
        return
    statement = insert(price_snapshots).values(rows)
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_price_day",
            set_={
                "price": statement.excluded.price,
                "market_cap": statement.excluded.market_cap,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
    )


def latest_prices(
    conn: Connection, market: Market, tickers: Sequence[str]
) -> dict[str, Quote]:
    """The most recent stored price per ticker, whatever day it came from. The caller
    decides whether it is fresh enough to serve or must be re-fetched."""
    if not tickers:
        return {}
    rows = conn.execute(
        select(price_snapshots)
        .where(
            price_snapshots.c.market == market.value,
            price_snapshots.c.ticker.in_(tickers),
        )
        .order_by(price_snapshots.c.ticker, price_snapshots.c.fetched_at.desc())
    ).mappings()

    out: dict[str, Quote] = {}
    for r in rows:
        if r["ticker"] in out:
            continue  # ordered desc, so the first hit is the newest
        out[r["ticker"]] = Quote(
            ticker=r["ticker"],
            price=r["price"],
            as_of=r["fetched_at"],
            market_cap=r["market_cap"],
        )
    return out


# --- Snapshots (the agent layer's time series) --------------------------------------


def write_portfolio_snapshot(
    conn: Connection,
    user_id: str,
    market: Market,
    captured_on: date,
    total_invested: Decimal,
    total_market_value: Decimal | None,
    pnl: Decimal | None,
    pnl_pct: Decimal | None,
    stock_count: int,
    sector_count: int,
    sector_allocations: dict[str, str],
) -> None:
    statement = insert(portfolio_snapshots).values(
        user_id=user_id,
        market=market.value,
        captured_on=captured_on,
        total_invested=total_invested,
        total_market_value=total_market_value,
        pnl=pnl,
        pnl_pct=pnl_pct,
        stock_count=stock_count,
        sector_count=sector_count,
        sector_allocations=sector_allocations,
    )
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_portfolio_day",
            set_={
                "total_invested": statement.excluded.total_invested,
                "total_market_value": statement.excluded.total_market_value,
                "pnl": statement.excluded.pnl,
                "pnl_pct": statement.excluded.pnl_pct,
                "stock_count": statement.excluded.stock_count,
                "sector_count": statement.excluded.sector_count,
                "sector_allocations": statement.excluded.sector_allocations,
            },
        )
    )


def get_portfolio_history(
    conn: Connection, user_id: str, market: Market, limit: int = 90
) -> list[dict]:
    rows = conn.execute(
        select(portfolio_snapshots)
        .where(
            portfolio_snapshots.c.user_id == user_id,
            portfolio_snapshots.c.market == market.value,
        )
        .order_by(portfolio_snapshots.c.captured_on.desc())
        .limit(limit)
    ).mappings()
    return [dict(r) for r in reversed(list(rows))]


# --- Insights (empty this phase; the agent layer writes here) -----------------------


def get_insights(conn: Connection, user_id: str, market: Market) -> list[dict]:
    rows = conn.execute(
        select(insights)
        .where(
            insights.c.user_id == user_id,
            insights.c.market == market.value,
            insights.c.dismissed.is_(False),
        )
        .order_by(insights.c.created_at.desc())
        .limit(50)
    ).mappings()
    return [dict(r) for r in rows]


def add_insight(
    conn: Connection,
    user_id: str,
    market: Market,
    severity: str,
    title: str,
    body: str,
    source: str,
    related_ticker: str | None = None,
    related_sector: str | None = None,
) -> None:
    conn.execute(
        insights.insert().values(
            user_id=user_id,
            market=market.value,
            severity=severity,
            title=title,
            body=body,
            source=source,
            related_ticker=related_ticker,
            related_sector=related_sector,
        )
    )
