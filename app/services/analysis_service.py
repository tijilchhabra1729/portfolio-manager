"""The service seam for the LLM features: Explore, the weekly report, and on-demand
analyst runs. Each ties the agents to the store and enforces the plan/cooldown/cap rules
in one place, so the API routes stay thin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.engine import Connection

from app.agents import health_report, explorer, runner
from app.config import settings
from app.core.sectors import Market
from app.llm.select import any_llm_configured, select_model
from app.market.fundamentals import YFinanceFundamentals
from app.market.news import YFinanceNewsProvider
from app.services import dashboard_service
from app.store import agent_repo

log = logging.getLogger(__name__)

_fundamentals = YFinanceFundamentals()
_news = YFinanceNewsProvider()


# --- Explore ------------------------------------------------------------------------


@dataclass
class ExploreResult:
    ticker: str
    fundamentals: dict | None
    read: dict | None
    source: str | None
    cached: bool
    used: int
    limit: int | None  # None = unlimited (premium)
    error: str | None = None


def explore(
    conn: Connection, user_id: str, market: Market, ticker: str, *, force: bool = False
) -> ExploreResult:
    cfg = settings()
    plan = agent_repo.get_plan(conn, user_id)
    premium = plan == "premium"
    limit = None if premium else cfg.free_explore_daily_limit
    today = date.today()
    used = agent_repo.explore_count_today(conn, user_id, today)

    # A fresh, in-TTL cached analysis serves everyone for free and never touches the cap.
    if not force:
        cached = agent_repo.get_cached_analysis(conn, market, ticker, cfg.analysis_ttl_hours)
        if cached:
            body = cached["body"]
            return ExploreResult(
                ticker=ticker.upper(),
                fundamentals=body.get("fundamentals"),
                read=body.get("read"),
                source=cached["generated_by"],
                cached=True,
                used=used,
                limit=limit,
            )

    fundamentals = _fundamentals.get_fundamentals(market, ticker)
    if fundamentals is None:
        return ExploreResult(
            ticker=ticker.upper(), fundamentals=None, read=None, source=None,
            cached=False, used=used, limit=limit,
            error=f"No data for {ticker.upper()} on {market.value}. Check the symbol.",
        )
    fund_dict = fundamentals.as_dict()

    # A fresh generation is what costs. Enforce the free cap here, before any LLM call.
    if limit is not None and used >= limit:
        return ExploreResult(
            ticker=ticker.upper(), fundamentals=fund_dict, read=None, source=None,
            cached=False, used=used, limit=limit,
            error=f"Free plan allows {limit} analyses a day. Upgrade for unlimited, or try again tomorrow.",
        )

    model = select_model(plan)
    if model is None:
        # Ratios are still useful without a model; just no written read.
        return ExploreResult(
            ticker=ticker.upper(), fundamentals=fund_dict, read=None, source=None,
            cached=False, used=used, limit=limit,
            error="No analysis model configured — showing the raw ratios only.",
        )

    headlines = _safe_news(market, ticker)
    read, err = explorer.run(fundamentals, headlines, model)
    if read is None:
        # The model was tried and failed. Don't burn a daily slot on a failure.
        return ExploreResult(
            ticker=ticker.upper(), fundamentals=fund_dict, read=None, source=None,
            cached=False, used=used, limit=limit,
            error="The analysis model didn't return a usable result. The ratios above are current.",
        )

    body = {"fundamentals": fund_dict, "read": read.model_dump()}
    agent_repo.save_analysis(conn, market, ticker, body, model.name)
    used = agent_repo.increment_explore(conn, user_id, today)
    return ExploreResult(
        ticker=ticker.upper(), fundamentals=fund_dict, read=read.model_dump(),
        source=model.name, cached=False, used=used, limit=limit,
    )


def _safe_news(market: Market, ticker: str):
    try:
        return _news.get_news(market, [ticker], per_ticker=4)
    except Exception:
        log.debug("explore news fetch failed for %s", ticker, exc_info=True)
        return []


# --- on-demand analyst (the Analyze-now button) -------------------------------------


@dataclass
class AnalyzeResult:
    rules_published: int
    analyst_ran: bool
    analyst_published: int
    skipped_reason: str | None


def analyze_now(conn: Connection, user_id: str, market: Market) -> AnalyzeResult:
    """Rules always rerun (cheap and deterministic). The LLM analyst runs only if its last
    output is older than the cooldown, so hammering the button can't re-bill it."""
    view = dashboard_service.build(conn, user_id, market)
    history = dashboard_service.history(conn, user_id, market)
    rules = runner.run_rules(conn, user_id, view, history)

    plan = agent_repo.get_plan(conn, user_id)
    model = select_model(plan)
    if model is None:
        reason = "No analysis model configured." if not any_llm_configured() else "No model for this plan."
        return AnalyzeResult(rules, False, 0, reason)

    last = agent_repo.latest_insight_time(conn, user_id, market, model.name)
    cooldown = timedelta(minutes=settings().analyst_cooldown_minutes)
    if last is not None and datetime.now(UTC) - last < cooldown:
        mins = settings().analyst_cooldown_minutes
        return AnalyzeResult(rules, False, 0, f"News analysis was just refreshed — available again in up to {mins} min.")

    published = runner.run_analyst(conn, user_id, view, model)
    return AnalyzeResult(rules, True, published, None)


# --- weekly report ------------------------------------------------------------------


def week_start(day: date | None = None) -> date:
    day = day or date.today()
    return day - timedelta(days=day.weekday())  # Monday


def generate_report(conn: Connection, user_id: str, market: Market) -> dict:
    view = dashboard_service.build(conn, user_id, market)
    history = dashboard_service.history(conn, user_id, market)
    plan = agent_repo.get_plan(conn, user_id)
    model = select_model(plan)

    score, narrative, flags = health_report.generate(view, history, model)
    period = week_start()
    agent_repo.upsert_report(
        conn, user_id, market, period,
        health_score=score, body=narrative, red_flags=flags,
        generated_by=(model.name if model else "rules"),
    )
    return latest_report(conn, user_id, market)


def latest_report(conn: Connection, user_id: str, market: Market) -> dict | None:
    row = agent_repo.latest_report(conn, user_id, market)
    if not row:
        return None
    return {
        "market": market.value,
        "period_start": row["period_start"].isoformat(),
        "health_score": row["health_score"],
        "narrative": row["body"],
        "red_flags": row["red_flags"] or [],
        "generated_by": row["generated_by"],
        "generated_at": row["created_at"].isoformat(),
    }
