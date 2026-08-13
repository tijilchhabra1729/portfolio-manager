from __future__ import annotations

import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents import runner
from app.api import serializers
from app.auth.supabase_jwt import current_user, require_refresh_token
from app.core.sectors import Market
from app.exporters.excel_dashboard import export
from app.llm.select import select_model
from app.services import analysis_service, briefing_service, dashboard_service
from app.store import agent_repo, repository
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


@router.post("/{market}/analyze")
def analyze(market: Market, user_id: str = Depends(current_user)):
    """The Analyze-now button: rerun the rule agents (cheap) and, if the cooldown has
    elapsed, the LLM news analyst. Returns what ran so the UI can explain a skip."""
    with connect() as conn:
        result = analysis_service.analyze_now(conn, user_id, market)
    return {
        "rules_published": result.rules_published,
        "analyst_ran": result.analyst_ran,
        "analyst_published": result.analyst_published,
        "skipped_reason": result.skipped_reason,
    }


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
    from datetime import date

    weekly_slot = date.today().weekday() == 0  # Monday: also refresh the weekly report
    written = []
    with connect() as conn:
        for user_id in repository.get_user_ids(conn):
            plan = agent_repo.get_plan(conn, user_id)
            model = select_model(plan)
            for market in Market:
                # Snapshot first so drift and the report read fresh history.
                view = dashboard_service.snapshot(conn, user_id, market)
                history = dashboard_service.history(conn, user_id, market)

                # Agents run per user+market and must never abort the loop — a failure
                # for one user's market shouldn't cost everyone else their refresh.
                try:
                    runner.run_rules(conn, user_id, view, history)
                    if model is not None:
                        runner.run_analyst(conn, user_id, view, model)
                    if weekly_slot:
                        analysis_service.generate_report(conn, user_id, market)
                except Exception:  # noqa: BLE001 — deliberately swallow per user+market
                    import logging

                    logging.getLogger(__name__).warning(
                        "agents failed for %s/%s", user_id, market.value, exc_info=True
                    )

                written.append(
                    {
                        "user": user_id,
                        "market": market.value,
                        "stocks": view.totals.stock_count,
                        "unpriced": list(view.unpriced),
                    }
                )

    # The hierarchical briefing is the expensive weekly job (one LLM call per held sector
    # + per market + the orchestrator). It runs in its own pass, AFTER the snapshot
    # connection above is released, because briefing_service holds no connection while the
    # agent graph makes its ~10 calls. A per-user failure is logged, never fatal.
    briefings_written = 0
    if weekly_slot:
        with connect() as conn:
            user_ids = repository.get_user_ids(conn)
        for user_id in user_ids:
            try:
                briefing_service.generate(user_id, force=True)
                briefings_written += 1
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "briefing failed for %s", user_id, exc_info=True
                )

    return {
        "ok": True,
        "snapshots": written,
        "weekly_report": weekly_slot,
        "briefings": briefings_written,
    }
