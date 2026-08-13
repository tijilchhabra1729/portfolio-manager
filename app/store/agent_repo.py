"""SQL for the phase-2 agentic layer: insights dedupe, plans, reports, analysis cache,
and the Explore usage counter. Kept beside repository.py rather than inside it so the
dashboard's data access and the agents' stay legible, but the rule is the same — this is
the only place these tables are touched, callers speak in dicts and domain values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from app.core.sectors import Market
from app.store.tables import (
    briefings,
    explore_usage,
    insights,
    reports,
    stock_analyses,
    user_plans,
)

# --- insights: dedupe-aware publishing ----------------------------------------------


def upsert_insight(
    conn: Connection,
    user_id: str,
    market: Market,
    *,
    severity: str,
    title: str,
    body: str,
    source: str,
    dedupe_key: str,
    related_ticker: str | None = None,
    related_sector: str | None = None,
) -> None:
    """Write an insight, or refresh the existing one with the same identity.

    On conflict we update the wording/severity but deliberately leave `dismissed` and
    `created_at` untouched: a condition the user already dismissed must stay dismissed
    while it persists, and the row keeps its original age.
    """
    statement = insert(insights).values(
        user_id=user_id,
        market=market.value,
        severity=severity,
        title=title,
        body=body,
        source=source,
        dedupe_key=dedupe_key,
        related_ticker=related_ticker,
        related_sector=related_sector,
    )
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_insight_key",
            set_={
                "severity": statement.excluded.severity,
                "title": statement.excluded.title,
                "body": statement.excluded.body,
                "related_ticker": statement.excluded.related_ticker,
                "related_sector": statement.excluded.related_sector,
            },
        )
    )


def delete_insights_for_source(
    conn: Connection, user_id: str, market: Market, source: str, keep_keys: Iterable[str]
) -> None:
    """Drop this source's insights whose condition no longer holds.

    A rule agent emits a key for every condition currently true; a key it *didn't* emit
    means the condition cleared, so its row is removed. This is what makes the panel
    self-healing — resolve a concentration and its warning disappears on the next run.
    """
    keep = list(keep_keys)
    condition = and_(
        insights.c.user_id == user_id,
        insights.c.market == market.value,
        insights.c.source == source,
    )
    if keep:
        condition = and_(condition, insights.c.dedupe_key.notin_(keep))
    conn.execute(delete(insights).where(condition))


def prune_insights_older_than(
    conn: Connection, user_id: str, market: Market, source: str, days: int
) -> None:
    """Event-style insights (the news analyst) expire; conditions do not."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    conn.execute(
        delete(insights).where(
            insights.c.user_id == user_id,
            insights.c.market == market.value,
            insights.c.source == source,
            insights.c.created_at < cutoff,
        )
    )


def latest_insight_time(
    conn: Connection, user_id: str, market: Market, source: str
) -> datetime | None:
    return conn.execute(
        select(func.max(insights.c.created_at)).where(
            insights.c.user_id == user_id,
            insights.c.market == market.value,
            insights.c.source == source,
        )
    ).scalar_one_or_none()


def dismiss_insight(conn: Connection, user_id: str, insight_id: int) -> bool:
    """Owner-scoped: the user_id predicate means one user cannot dismiss another's
    insight even by guessing an id. Returns whether a row was actually updated."""
    result = conn.execute(
        update(insights)
        .where(insights.c.id == insight_id, insights.c.user_id == user_id)
        .values(dismissed=True)
    )
    return result.rowcount > 0


# --- plans --------------------------------------------------------------------------


def get_plan(conn: Connection, user_id: str) -> str:
    plan = conn.execute(
        select(user_plans.c.plan).where(user_plans.c.user_id == user_id)
    ).scalar_one_or_none()
    return plan or "free"  # no row = free; the app never requires a row to exist


def set_plan(
    conn: Connection,
    user_id: str,
    plan: str,
    *,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    status: str | None = None,
) -> None:
    values = {"user_id": user_id, "plan": plan, "updated_at": datetime.now(UTC)}
    updated = {"plan": plan, "updated_at": datetime.now(UTC)}
    # Only overwrite Stripe fields when supplied, so a plain test-mode flip doesn't wipe
    # a real customer id.
    for key, val in (
        ("stripe_customer_id", stripe_customer_id),
        ("stripe_subscription_id", stripe_subscription_id),
        ("status", status),
    ):
        if val is not None:
            values[key] = val
            updated[key] = val

    statement = insert(user_plans).values(**values)
    conn.execute(statement.on_conflict_do_update(index_elements=["user_id"], set_=updated))


def get_plan_row(conn: Connection, user_id: str) -> dict | None:
    row = conn.execute(
        select(user_plans).where(user_plans.c.user_id == user_id)
    ).mappings().first()
    return dict(row) if row else None


def find_user_by_customer(conn: Connection, customer_id: str) -> str | None:
    return conn.execute(
        select(user_plans.c.user_id).where(
            user_plans.c.stripe_customer_id == customer_id
        )
    ).scalar_one_or_none()


# --- reports ------------------------------------------------------------------------


def upsert_report(
    conn: Connection,
    user_id: str,
    market: Market,
    period_start: date,
    *,
    health_score: int,
    body: str,
    red_flags: list[dict],
    generated_by: str,
) -> None:
    statement = insert(reports).values(
        user_id=user_id,
        market=market.value,
        period_start=period_start,
        health_score=health_score,
        body=body,
        red_flags=red_flags,
        generated_by=generated_by,
    )
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_report_week",
            set_={
                "health_score": statement.excluded.health_score,
                "body": statement.excluded.body,
                "red_flags": statement.excluded.red_flags,
                "generated_by": statement.excluded.generated_by,
                "created_at": func.now(),
            },
        )
    )


def latest_report(conn: Connection, user_id: str, market: Market) -> dict | None:
    row = conn.execute(
        select(reports)
        .where(reports.c.user_id == user_id, reports.c.market == market.value)
        .order_by(reports.c.period_start.desc())
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


# --- briefings (phase 3: the weekly cross-market hierarchical briefing) --------------


def upsert_briefing(
    conn: Connection,
    user_id: str,
    period_start: date,
    *,
    headline: str,
    overall_direction: str,
    body: dict,
    generated_by: str,
) -> None:
    """Write this week's briefing, or overwrite it if it's regenerated in the same week.
    Cross-market, so there is no market in the key — one briefing per user per week."""
    statement = insert(briefings).values(
        user_id=user_id,
        period_start=period_start,
        headline=headline,
        overall_direction=overall_direction,
        body=body,
        generated_by=generated_by,
    )
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_briefing_week",
            set_={
                "headline": statement.excluded.headline,
                "overall_direction": statement.excluded.overall_direction,
                "body": statement.excluded.body,
                "generated_by": statement.excluded.generated_by,
                "created_at": func.now(),
            },
        )
    )


def latest_briefing(conn: Connection, user_id: str) -> dict | None:
    row = conn.execute(
        select(briefings)
        .where(briefings.c.user_id == user_id)
        .order_by(briefings.c.period_start.desc())
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


def latest_briefing_time(conn: Connection, user_id: str) -> datetime | None:
    """When the newest briefing was written, for the on-demand cooldown."""
    return conn.execute(
        select(func.max(briefings.c.created_at)).where(briefings.c.user_id == user_id)
    ).scalar_one_or_none()


# --- stock analysis cache (shared, not user-scoped) ---------------------------------


def get_cached_analysis(
    conn: Connection, market: Market, ticker: str, max_age_hours: int
) -> dict | None:
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    row = conn.execute(
        select(stock_analyses).where(
            stock_analyses.c.market == market.value,
            stock_analyses.c.ticker == ticker.upper(),
            stock_analyses.c.generated_at >= cutoff,
        )
    ).mappings().first()
    return dict(row) if row else None


def save_analysis(
    conn: Connection, market: Market, ticker: str, body: dict, generated_by: str
) -> None:
    statement = insert(stock_analyses).values(
        market=market.value,
        ticker=ticker.upper(),
        body=body,
        generated_by=generated_by,
        generated_at=datetime.now(UTC),
    )
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_stock_analysis",
            set_={
                "body": statement.excluded.body,
                "generated_by": statement.excluded.generated_by,
                "generated_at": statement.excluded.generated_at,
            },
        )
    )


# --- explore usage (the free-plan daily cap) ----------------------------------------


def explore_count_today(conn: Connection, user_id: str, day: date) -> int:
    return conn.execute(
        select(explore_usage.c.count).where(
            explore_usage.c.user_id == user_id, explore_usage.c.day == day
        )
    ).scalar_one_or_none() or 0


def increment_explore(conn: Connection, user_id: str, day: date) -> int:
    """Atomically bump today's counter and return the new value."""
    statement = insert(explore_usage).values(user_id=user_id, day=day, count=1)
    conn.execute(
        statement.on_conflict_do_update(
            constraint="uq_explore_usage_day",
            set_={"count": explore_usage.c.count + 1},
        )
    )
    return explore_count_today(conn, user_id, day)
