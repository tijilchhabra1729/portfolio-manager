"""Orchestrates the agents and publishes their output with dedupe.

Rule agents describe *conditions* — they're re-derived every run, so their rows are
reconciled: upsert what's true now, delete what's no longer true (per source, including
sources that went silent). The news analyst describes *events* — its rows are upserted and
then aged out after a week, never deleted just for not reappearing.

Every LLM step is wrapped: a model failure is logged and the rule insights still publish.
An analyst error must never take down a refresh.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

from app.agents import analyst, concentration, drift, small_cap
from app.agents.base import InsightDraft
from app.core.models import DashboardView
from app.llm.base import AnalystModel
from app.market.news import NewsProvider, YFinanceNewsProvider
from app.store import agent_repo

log = logging.getLogger(__name__)

# Every source a rule agent can emit. Listed explicitly so a condition that clears to
# nothing still gets its stale rows deleted (an emitted-keys set of {} means "delete all
# of this source").
RULE_SOURCES = (concentration.SOURCE, small_cap.SOURCE, drift.SOURCE)
ANALYST_PRUNE_DAYS = 7

_default_news = YFinanceNewsProvider()


def run_rules(
    conn: Connection, user_id: str, view: DashboardView, history: list[dict]
) -> int:
    drafts: list[InsightDraft] = []
    drafts += concentration.run(view)
    drafts += small_cap.run(view)
    drafts += drift.run(view, history)
    _publish(conn, user_id, view, drafts)

    # Reconcile each rule source against what it just emitted.
    emitted: dict[str, list[str]] = {s: [] for s in RULE_SOURCES}
    for d in drafts:
        emitted[d.source].append(d.dedupe_key)
    for source, keys in emitted.items():
        agent_repo.delete_insights_for_source(conn, user_id, view.market, source, keys)

    return len(drafts)


def run_analyst(
    conn: Connection,
    user_id: str,
    view: DashboardView,
    model: AnalystModel,
    *,
    news_provider: NewsProvider | None = None,
) -> int:
    if not view.stocks:
        return 0
    provider = news_provider or _default_news
    try:
        headlines = provider.get_news(view.market, [s.ticker for s in view.stocks])
        drafts = analyst.run(view, headlines, model)
    except Exception:
        # News fetch or model call blew up — log and leave the rule insights standing.
        log.warning("analyst run failed for %s/%s", user_id, view.market.value, exc_info=True)
        return 0

    _publish(conn, user_id, view, drafts)
    # Age out old analyst rows (both provider sources), so week-old headlines don't linger.
    for source in (analyst.SOURCE_GROQ, analyst.SOURCE_CLAUDE):
        agent_repo.prune_insights_older_than(
            conn, user_id, view.market, source, ANALYST_PRUNE_DAYS
        )
    return len(drafts)


def _publish(
    conn: Connection, user_id: str, view: DashboardView, drafts: list[InsightDraft]
) -> None:
    for d in drafts:
        agent_repo.upsert_insight(
            conn,
            user_id,
            view.market,
            severity=d.severity,
            title=d.title,
            body=d.body,
            source=d.source,
            dedupe_key=d.dedupe_key,
            related_ticker=d.related_ticker,
            related_sector=d.related_sector,
        )
