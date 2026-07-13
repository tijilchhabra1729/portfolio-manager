"""Schema.

Money and units are NUMERIC, never DOUBLE PRECISION. Every user-owned table carries a
user_id from day one: multi-user is out of scope, but the column costs nothing now and
turns "support multiple users" into a row-level-security policy rather than a migration
across the whole application.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

AMOUNT = Numeric(20, 4)
QUANTITY = Numeric(20, 6)  # 6dp so US fractional shares survive intact
PERCENT = Numeric(10, 4)

instruments = Table(
    "instruments",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("market", String(8), nullable=False),
    Column("ticker", String(24), nullable=False),
    Column("name", String(160), nullable=False),
    Column("sector", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "market", "ticker", name="uq_instrument"),
)

# Append-only. A position is replayed from these rows rather than stored, so the
# portfolio always has a full audit trail and an agent can reason over its history.
transactions = Table(
    "transactions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("market", String(8), nullable=False),
    Column("ticker", String(24), nullable=False),
    Column("txn_type", String(8), nullable=False),  # BUY | SELL
    Column("units", QUANTITY, nullable=False),
    Column("price_per_unit", AMOUNT, nullable=False),
    Column("txn_date", Date, nullable=False),
    Column("source_file", String(255)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_txn_owner", "user_id", "market"),
)

# Doubles as the price cache and the price history. One row per ticker per day: a
# refresh overwrites today's row, so the latest price is always current while yesterday's
# stays put for the agent layer to look back on.
price_snapshots = Table(
    "price_snapshots",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("market", String(8), nullable=False),
    Column("ticker", String(24), nullable=False),
    Column("price", AMOUNT, nullable=False),
    Column("market_cap", Numeric(24, 2)),
    Column("captured_on", Date, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("market", "ticker", "captured_on", name="uq_price_day"),
)

# Written by the daily job. Exists purely for the agent layer -- an agent asking "is my
# IT exposure drifting?" needs a time series, and history not captured today cannot be
# recovered later. sector_allocations holds {sector: pct} so drift is queryable without
# replaying the whole ledger.
portfolio_snapshots = Table(
    "portfolio_snapshots",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("market", String(8), nullable=False),
    Column("captured_on", Date, nullable=False),
    Column("total_invested", AMOUNT, nullable=False),
    Column("total_market_value", AMOUNT),
    Column("pnl", AMOUNT),
    Column("pnl_pct", PERCENT),
    Column("stock_count", Integer, nullable=False),
    Column("sector_count", Integer, nullable=False),
    Column("sector_allocations", JSONB),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "market", "captured_on", name="uq_portfolio_day"),
)

# Empty in this phase. The endpoint and the UI panel already read it, so an agent that
# writes a row here shows up in the dashboard with no frontend work at all.
insights = Table(
    "insights",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("market", String(8), nullable=False),
    Column("severity", String(16), nullable=False, server_default="info"),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("related_ticker", String(24)),
    Column("related_sector", String(64)),
    Column("source", String(64), nullable=False, server_default="system"),
    Column("dismissed", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_insight_owner", "user_id", "market", "dismissed"),
)
