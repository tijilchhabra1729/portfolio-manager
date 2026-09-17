"""Assembles a dashboard from the ledger, the instruments, and the prices.

This is the function the agent layer will call. It returns a DashboardView -- a plain
domain object, no database rows and no JSON -- so an agent, the Excel exporter, the API
and the daily job all consume the same thing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.engine import Connection

from app.core.calculations import build_dashboard, replay_ledger
from app.core.models import DashboardView
from app.core.sectors import Market
from app.market.cache import PriceService
from app.store import repository

_prices = PriceService()


def build(
    conn: Connection,
    user_id: str,
    market: Market,
    *,
    force_refresh: bool = False,
) -> DashboardView:
    transactions = repository.get_transactions(conn, user_id, market)
    # One FIFO pass gives both what is held and what every priced sell realised.
    replay = replay_ledger(transactions)
    positions = replay.positions
    instruments = repository.get_instruments(conn, user_id, market)
    quotes = _prices.get_prices(
        conn, market, list(positions), force=force_refresh
    )
    # The two user-entered numbers (investable pot, options income) and yesterday's
    # prices for the day-change column. All optional: absent, the view simply omits them.
    settings_row = repository.get_settings(conn, user_id, market) or {}
    prev_closes = repository.previous_closes(
        conn, market, list(positions), before=date.today()
    )
    return build_dashboard(
        market,
        positions,
        instruments,
        quotes,
        realised=replay.realised_pnl,
        options_income=settings_row.get("options_income") or Decimal(0),
        investable=settings_row.get("total_investable"),
        prev_closes=prev_closes,
    )


def snapshot(conn: Connection, user_id: str, market: Market) -> DashboardView:
    """Build the dashboard and record today's totals.

    Called by the daily job. The snapshot exists for the agent layer: allocation drift
    is only answerable against a time series, and a day not captured is a day lost.
    """
    view = build(conn, user_id, market, force_refresh=True)
    repository.write_portfolio_snapshot(
        conn,
        user_id=user_id,
        market=market,
        captured_on=date.today(),
        total_invested=view.totals.invested,
        total_market_value=view.totals.market_value,
        pnl=view.totals.pnl,
        pnl_pct=view.totals.pnl_pct,
        stock_count=view.totals.stock_count,
        sector_count=view.totals.sector_count,
        sector_allocations={s.sector: str(s.allocation_pct) for s in view.sectors},
    )
    return view


def history(conn: Connection, user_id: str, market: Market, limit: int = 90) -> list[dict]:
    return repository.get_portfolio_history(conn, user_id, market, limit)
