"""The runner's publishing behaviour — dedupe, self-healing, prune, isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.agents import runner
from app.core.sectors import Market
from app.store import repository
from tests.conftest import FakeModel
from tests.factories import sector, stock, view

ALICE = "alice"
BOB = "bob"


def _insights(conn, user, market=Market.INDIA):
    return repository.get_insights(conn, user, market)


def test_rules_publish_then_dedupe_on_rerun(conn):
    v = view([stock("BIG", "IT", 25)], [sector("IT", 25)])
    runner.run_rules(conn, ALICE, v, [])
    first = _insights(conn, ALICE)
    assert len(first) == 2  # stock:BIG + sector:IT

    runner.run_rules(conn, ALICE, v, [])  # same conditions
    assert len(_insights(conn, ALICE)) == 2  # not 4 — upserted, not duplicated


def test_cleared_condition_deletes_the_row(conn):
    hot = view([stock("BIG", "IT", 25)], [sector("IT", 25)])
    runner.run_rules(conn, ALICE, hot, [])
    assert len(_insights(conn, ALICE)) == 2

    # Portfolio rebalanced — nothing is concentrated any more.
    calm = view([stock("A", "IT", 8), stock("B", "Auto", 8)], [sector("IT", 8), sector("Auto", 8)])
    runner.run_rules(conn, ALICE, calm, [])
    assert _insights(conn, ALICE) == []  # the warnings self-heal


def test_dismissed_insight_stays_dismissed_while_condition_persists(conn):
    v = view([stock("BIG", "IT", 25)], [sector("IT", 25)])
    runner.run_rules(conn, ALICE, v, [])
    target = next(i for i in _insights(conn, ALICE) if i["source"] == "concentration" and i["related_ticker"] == "BIG")

    # User dismisses it.
    conn.execute(text("UPDATE insights SET dismissed = true WHERE id = :id"), {"id": target["id"]})
    assert not any(i["id"] == target["id"] for i in _insights(conn, ALICE))

    # The condition still holds; a rerun must not resurrect the dismissed card.
    runner.run_rules(conn, ALICE, v, [])
    assert not any(i["id"] == target["id"] for i in _insights(conn, ALICE))


def test_analyst_insights_are_pruned_after_a_week(conn):
    v = view([stock("RELIANCE", "Energy", 50), stock("INFY", "IT", 50)], [sector("Energy", 50), sector("IT", 50)])

    class News:
        def get_news(self, *a, **k):
            from app.market.news import Headline
            return [Headline("RELIANCE", "Big Reliance news", "", "Wire", datetime.now(UTC))]

    model = FakeModel("groq", [{"insights": [
        {"severity": "warning", "title": "Reliance data-centre deal", "body": "Impacts Reliance.", "related_ticker": "RELIANCE"}
    ]}])
    runner.run_analyst(conn, ALICE, v, model, news_provider=News())
    rows = _insights(conn, ALICE)
    assert any(r["source"] == "groq" for r in rows)

    # Age the row past the 7-day window; the next run prunes it.
    conn.execute(text("UPDATE insights SET created_at = :old WHERE source = 'groq'"),
                 {"old": datetime.now(UTC) - timedelta(days=8)})
    runner.run_analyst(conn, ALICE, v, FakeModel("groq", [{"insights": []}]), news_provider=News())
    assert not any(r["source"] == "groq" for r in _insights(conn, ALICE))


def test_one_users_insights_never_touch_anothers(conn):
    v = view([stock("BIG", "IT", 25)], [sector("IT", 25)])
    runner.run_rules(conn, ALICE, v, [])
    runner.run_rules(conn, BOB, v, [])
    # Bob rebalances; Alice's warnings must survive Bob's reconcile-delete.
    calm = view([stock("A", "IT", 8), stock("B", "Auto", 8)], [sector("IT", 8), sector("Auto", 8)])
    runner.run_rules(conn, BOB, calm, [])

    assert len(_insights(conn, ALICE)) == 2
    assert _insights(conn, BOB) == []


def test_analyst_failure_does_not_break_the_run(conn):
    v = view([stock("A", "IT", 50), stock("B", "Auto", 50)], [sector("IT", 50), sector("Auto", 50)])

    class ExplodingNews:
        def get_news(self, *a, **k):
            raise RuntimeError("yahoo down")

    # Should log-and-skip, returning 0, not raise.
    assert runner.run_analyst(conn, ALICE, v, FakeModel(), news_provider=ExplodingNews()) == 0
