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
    # A stable identity for the condition/event an insight describes, so a nightly rerun
    # updates the same row instead of stacking a duplicate. Nullable for phase-1 rows
    # written before the column existed.
    Column("dedupe_key", String(120)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_insight_owner", "user_id", "market", "dismissed"),
    UniqueConstraint("user_id", "market", "source", "dedupe_key", name="uq_insight_key"),
)

# Who is on which plan. No row means free; the app never assumes a row exists. Stripe IDs
# are null until a real subscription is created (they stay null forever in test mode).
user_plans = Table(
    "user_plans",
    metadata,
    Column("user_id", String(64), primary_key=True),
    Column("plan", String(16), nullable=False, server_default="free"),  # free | premium
    Column("stripe_customer_id", String(64)),
    Column("stripe_subscription_id", String(64)),
    Column("status", String(32)),  # Stripe subscription status, informational
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# One weekly health report per user per market. period_start is the Monday of the week, so
# regenerating within the same week overwrites rather than piling up.
reports = Table(
    "reports",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("market", String(8), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("health_score", Integer, nullable=False),
    Column("body", Text, nullable=False),
    Column("red_flags", JSONB),  # [{ok: bool, text: str}, ...]
    Column("generated_by", String(16), nullable=False, server_default="system"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "market", "period_start", name="uq_report_week"),
)

# A shared cache of the Explore tab's LLM read. NOT scoped to a user -- fundamentals and
# news are public, so one analysis of RELIANCE serves everyone and is billed once. The
# per-user daily cap lives in explore_usage, not here.
stock_analyses = Table(
    "stock_analyses",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("market", String(8), nullable=False),
    Column("ticker", String(24), nullable=False),
    Column("body", JSONB, nullable=False),  # the structured read + the raw ratios
    Column("generated_by", String(16), nullable=False, server_default="system"),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("market", "ticker", name="uq_stock_analysis"),
)

# The free-plan daily allowance. One row per user per day; only a *fresh* Explore
# generation (a cache miss) increments the count -- reopening a cached analysis is free.
explore_usage = Table(
    "explore_usage",
    metadata,
    Column("user_id", String(64), nullable=False),
    Column("day", Date, nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
    UniqueConstraint("user_id", "day", name="uq_explore_usage_day"),
)

# One hierarchical briefing per user per week, spanning BOTH markets -- the orchestrator
# agent sits above the market agents, so a briefing is a single cross-market document, not
# one per market (that is why there is no market column). period_start is the Monday of the
# week, so regenerating within the week overwrites. body holds the whole nested doc
# (markets -> sectors, each with a direction) that the Briefing tab renders.
briefings = Table(
    "briefings",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("headline", String(200), nullable=False),
    Column("overall_direction", String(16), nullable=False, server_default="neutral"),
    Column("body", JSONB, nullable=False),
    Column("generated_by", String(16), nullable=False, server_default="system"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "period_start", name="uq_briefing_week"),
)
