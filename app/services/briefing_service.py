"""The service seam for the hierarchical briefing.

It does three things in order, and the order is the point: gather the inputs under a short
DB connection, run the LangGraph briefing **with no connection held** (it makes ~10 slow
LLM/network calls, and holding a pooled Supabase connection across that would starve the
pool), then reopen a connection to persist. This differs deliberately from `analyze_now`,
which holds its connection because it makes at most one model call.

Weekly + on-demand: the cron calls `generate(force=True)` on Mondays; the button calls
`generate()` which is cooldown-guarded so it can't re-bill the whole agent tree on a
double-click.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from app.agents.briefing import graph
from app.config import settings
from app.core.models import DashboardView, StockRow
from app.core.sectors import Market
from app.llm.select import select_model
from app.services import dashboard_service
from app.store import agent_repo
from app.store.db import connect

log = logging.getLogger(__name__)


def latest(user_id: str) -> dict | None:
    with connect() as conn:
        row = agent_repo.latest_briefing(conn, user_id)
    return _shape(row) if row else None


def generate(user_id: str, *, force: bool = False) -> dict:
    """Build this week's briefing. Returns {generated, skipped_reason, briefing}.

    On the on-demand path (`force=False`) a recent briefing short-circuits the whole run —
    the existing one is returned rather than re-billing the agent tree. The weekly cron
    passes `force=True` so it always produces the new week's document.
    """
    cfg = settings()

    if not force:
        with connect() as conn:
            last = agent_repo.latest_briefing_time(conn, user_id)
        if last is not None and datetime.now(UTC) - last < timedelta(minutes=cfg.briefing_cooldown_minutes):
            hours = max(1, cfg.briefing_cooldown_minutes // 60)
            with connect() as conn:
                existing = agent_repo.latest_briefing(conn, user_id)
            return {
                "generated": False,
                "skipped_reason": f"A briefing was generated recently — you can regenerate in up to {hours}h.",
                "briefing": _shape(existing) if existing else None,
            }

    # 1. Gather inputs under a short connection, then release it.
    with connect() as conn:
        plan = agent_repo.get_plan(conn, user_id)
        views = {market: dashboard_service.build(conn, user_id, market) for market in Market}

    model = select_model(plan)
    sector_tasks, market_ctx = _build_inputs(views, cfg.briefing_max_sectors_per_market)

    # 2. Run the graph with NO connection held (this is the slow part).
    body = graph.run_briefing(
        user_id=user_id,
        plan=plan,
        sector_tasks=sector_tasks,
        market_ctx=market_ctx,
        model=model,
        max_concurrency=cfg.briefing_max_concurrency,
    )

    # 3. Persist under a fresh short connection.
    period = _week_start()
    with connect() as conn:
        agent_repo.upsert_briefing(
            conn, user_id, period,
            headline=body["headline"],
            overall_direction=body["overall_direction"],
            body=body,
            generated_by=body.get("generated_by", "rules"),
        )
        row = agent_repo.latest_briefing(conn, user_id)

    return {"generated": True, "skipped_reason": None, "briefing": _shape(row)}


# --- input assembly -----------------------------------------------------------------


def _build_inputs(
    views: dict[Market, DashboardView], max_sectors: int
) -> tuple[list[dict], dict]:
    """Turn the two dashboards into the graph's inputs: one Send payload per held sector
    (top-N by allocation per market) plus per-market context for the orchestrator."""
    sector_tasks: list[dict] = []
    market_ctx: dict = {}

    for market, view in views.items():
        mv = market.value
        market_ctx[mv] = {
            "currency": view.currency,
            "pnl_pct": float(view.totals.pnl_pct) if view.totals.pnl_pct is not None else None,
        }
        if not view.stocks:
            continue

        by_sector: dict[str, list[StockRow]] = defaultdict(list)
        for s in view.stocks:
            by_sector[s.sector].append(s)

        # The most-invested sectors get an agent; the long tail is left off to bound cost.
        ranked = sorted(view.sectors, key=lambda x: x.allocation_pct, reverse=True)
        for sec in ranked[:max_sectors]:
            holdings = by_sector.get(sec.sector)
            if not holdings:
                continue
            sector_tasks.append(
                {
                    "market": mv,
                    "currency": view.currency,
                    "sector": sec.sector,
                    "holdings": [_holding(s) for s in holdings],
                }
            )

    return sector_tasks, market_ctx


def _holding(s: StockRow) -> dict:
    # allocation and P/L become floats here: they feed reasoning and a prompt, not the
    # ledger, and they never reach a money column — so float is fine and JSON-clean.
    return {
        "ticker": s.ticker,
        "name": s.name,
        "allocation_pct": float(s.allocation_pct),
        "pnl_pct": float(s.pnl_pct) if s.pnl_pct is not None else None,
        "cap_class": s.cap_class,
    }


def _shape(row: dict) -> dict:
    """The stored body already holds the whole nested doc; add the week and timestamp."""
    body = dict(row["body"])
    body["period_start"] = row["period_start"].isoformat()
    body["generated_at"] = row["created_at"].isoformat()
    return body


def _week_start(day: date | None = None) -> date:
    day = day or date.today()
    return day - timedelta(days=day.weekday())  # Monday
