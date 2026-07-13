"""DashboardView -> JSON.

Decimals go over the wire as strings. JSON numbers are IEEE doubles, so serialising
122525.00 as a number hands the browser a float and quietly undoes the care taken to
keep money exact all the way down. The frontend calls Number() only to format and to
plot -- never to compute.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.models import DashboardView, SectorRow, StockRow
from app.core.sectors import MARKETS


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _stock(row: StockRow) -> dict:
    return {
        "sno": row.sno,
        "ticker": row.ticker,
        "name": row.name,
        "sector": row.sector,
        "units": money(row.units),
        "invested": money(row.invested),
        "price": money(row.price),
        "market_value": money(row.market_value),
        "pnl": money(row.pnl),
        "pnl_pct": money(row.pnl_pct),
        "allocation_pct": money(row.allocation_pct),
        "stale_price": row.stale_price,
    }


def _sector(row: SectorRow) -> dict:
    return {
        "sno": row.sno,
        "sector": row.sector,
        "stock_count": row.stock_count,
        "invested": money(row.invested),
        "market_value": money(row.market_value),
        "pnl": money(row.pnl),
        "pnl_pct": money(row.pnl_pct),
        "allocation_pct": money(row.allocation_pct),
        "unpriced_count": row.unpriced_count,
    }


def dashboard(view: DashboardView) -> dict:
    return {
        "market": view.market.value,
        "label": MARKETS[view.market].label,
        "currency": view.currency,
        "symbol": view.symbol,
        "generated_at": view.generated_at.isoformat(),
        "stocks": [_stock(r) for r in view.stocks],
        "sectors": [_sector(r) for r in view.sectors],
        "totals": {
            "invested": money(view.totals.invested),
            "market_value": money(view.totals.market_value),
            "pnl": money(view.totals.pnl),
            "pnl_pct": money(view.totals.pnl_pct),
            "stock_count": view.totals.stock_count,
            "sector_count": view.totals.sector_count,
        },
        "unpriced": list(view.unpriced),
    }
