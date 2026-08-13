"""Builders for constructing DashboardViews in tests without a database.

The rule agents are pure functions over a view, so a test just needs a view with the
allocations it wants to probe — no upload, no pricing, no snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.core.models import DashboardView, SectorRow, StockRow, Totals
from app.core.sectors import Market

D = Decimal


def stock(ticker, sector, alloc, *, invested="10000", cap_class="large", pnl_pct="0"):
    return StockRow(
        sno=0, ticker=ticker, name=f"{ticker} Ltd", sector=sector,
        units=D("100"), invested=D(invested), allocation_pct=D(str(alloc)),
        pnl_pct=D(pnl_pct), cap_class=cap_class,
    )


def sector(name, alloc, *, count=1, invested="10000", pnl_pct="0"):
    return SectorRow(
        sno=0, sector=name, stock_count=count, invested=D(invested),
        allocation_pct=D(str(alloc)), pnl_pct=D(pnl_pct),
    )


def view(stocks, sectors, *, market=Market.INDIA, invested=None, unpriced=()):
    total = invested if invested is not None else sum((s.invested for s in stocks), D(0))
    return DashboardView(
        market=market, currency="INR", symbol="₹",
        stocks=tuple(stocks), sectors=tuple(sectors),
        totals=Totals(
            invested=D(str(total)), market_value=D(str(total)), pnl=D(0), pnl_pct=D(0),
            stock_count=len(stocks), sector_count=len(sectors),
        ),
        generated_at=datetime.now(UTC), unpriced=tuple(unpriced),
    )
