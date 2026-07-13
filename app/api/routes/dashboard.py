from __future__ import annotations

import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api import serializers
from app.auth.supabase_jwt import current_user, require_refresh_token
from app.core.sectors import Market
from app.exporters.excel_dashboard import export
from app.services import dashboard_service
from app.store import repository
from app.store.db import connect

router = APIRouter(prefix="/api", tags=["dashboard"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{market}/dashboard")
def dashboard(
    market: Market,
    refresh: bool = False,
    user_id: str = Depends(current_user),
):
    """refresh=true is the Refresh button: skip the price cache and go to the network."""
    with connect() as conn:
        view = dashboard_service.build(conn, user_id, market, force_refresh=refresh)
    return serializers.dashboard(view)


@router.get("/{market}/history")
def history(market: Market, user_id: str = Depends(current_user)):
    """Daily snapshots. Nothing in the UI plots these yet -- they exist so the agent
    layer has a time series to reason about drift over."""
    with connect() as conn:
        rows = dashboard_service.history(conn, user_id, market)
    return [
        {
            "date": r["captured_on"].isoformat(),
            "invested": str(r["total_invested"]),
            "market_value": None if r["total_market_value"] is None else str(r["total_market_value"]),
            "pnl": None if r["pnl"] is None else str(r["pnl"]),
            "pnl_pct": None if r["pnl_pct"] is None else str(r["pnl_pct"]),
            "sector_allocations": r["sector_allocations"],
        }
        for r in rows
    ]


@router.get("/{market}/export")
def export_excel(market: Market, user_id: str = Depends(current_user)):
    """The doc's original ask: the dashboard as a spreadsheet, tables plus charts."""
    with connect() as conn:
        view = dashboard_service.build(conn, user_id, market)
    workbook = export(view)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    name = f"portfolio_dashboard_{market.value.lower()}.xlsx"
    return StreamingResponse(
        buffer,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/refresh", dependencies=[Depends(require_refresh_token)])
def refresh_all():
    """Called daily by GitHub Actions. Re-prices every market and writes a snapshot.

    Snapshots every user who holds anything, rather than one hardcoded id: there is no
    logged-in user on a cron request, and in production holdings belong to a Supabase
    UUID, not the local dev user.

    Doubles as the keep-alive: Supabase pauses a free project after 7 days of
    inactivity, and this touching the database daily is what stops that happening.
    """
    written = []
    with connect() as conn:
        for user_id in repository.get_user_ids(conn):
            for market in Market:
                view = dashboard_service.snapshot(conn, user_id, market)
                written.append(
                    {
                        "user": user_id,
                        "market": market.value,
                        "stocks": view.totals.stock_count,
                        "unpriced": list(view.unpriced),
                    }
                )
    return {"ok": True, "snapshots": written}
