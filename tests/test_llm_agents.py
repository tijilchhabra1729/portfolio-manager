"""The three LLM agents, driven with FakeModel — no network, no key."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents import analyst, explorer, health_report
from app.market.fundamentals import Fundamentals
from app.market.news import Headline
from tests.conftest import FakeModel
from tests.factories import sector, stock, view

from decimal import Decimal

D = Decimal


def _headlines():
    return [Headline("RELIANCE", "Reliance signs data-centre deal", "big", "Wire", datetime.now(UTC))]


# --- analyst ------------------------------------------------------------------------


def test_analyst_maps_a_valid_reply_to_drafts():
    v = view([stock("RELIANCE", "Energy", 50), stock("INFY", "IT", 50)], [sector("Energy", 50), sector("IT", 50)])
    model = FakeModel("groq", [{"insights": [
        {"severity": "warning", "title": "Reliance data-centre deal boosts energy demand",
         "body": "The new facility runs on renewables.", "related_ticker": "RELIANCE", "related_sector": "Energy"}
    ]}])
    drafts = analyst.run(v, _headlines(), model)
    assert len(drafts) == 1
    assert drafts[0].source == "groq"
    assert drafts[0].related_ticker == "RELIANCE"
    assert drafts[0].dedupe_key.startswith("analyst:RELIANCE:")


def test_analyst_nulls_a_ticker_the_user_does_not_hold():
    v = view([stock("RELIANCE", "Energy", 100)], [sector("Energy", 100)])
    model = FakeModel("groq", [{"insights": [
        {"severity": "info", "title": "Adani news", "body": "About a stock you don't own.", "related_ticker": "ADANIENT"}
    ]}])
    assert analyst.run(v, _headlines(), model)[0].related_ticker is None


def test_analyst_retries_then_skips_on_bad_json():
    v = view([stock("RELIANCE", "Energy", 100)], [sector("Energy", 100)])
    # Two malformed replies -> retry exhausted -> empty, never raises.
    model = FakeModel("groq", [{"wrong": "shape"}, {"also": "wrong"}])
    # _Reply tolerates missing "insights" (defaults to []), so this yields no drafts.
    assert analyst.run(v, _headlines(), model) == []


def test_analyst_survives_a_model_exception():
    v = view([stock("RELIANCE", "Energy", 100)], [sector("Energy", 100)])
    from app.llm.base import AnalystError
    model = FakeModel("groq", [AnalystError("boom"), AnalystError("boom")])
    assert analyst.run(v, _headlines(), model) == []


def test_analyst_no_headlines_no_call():
    v = view([stock("A", "IT", 100)], [sector("IT", 100)])
    model = FakeModel("groq", [{"insights": []}])
    assert analyst.run(v, [], model) == []
    assert model.calls == []  # never bothered the model


# --- explorer -----------------------------------------------------------------------


def _fundamentals():
    return Fundamentals(
        ticker="RELIANCE", name="Reliance", sector="Energy", currency="INR",
        price=D("1300"), market_cap=D("17546000000000"), pe=D("21.7"), pb=D("1.9"),
        roe_pct=D("9.1"), debt_to_equity=D("36.6"), profit_margin_pct=D("7.6"),
        revenue_growth_pct=D("12.5"), dividend_yield_pct=D("0.46"), beta=D("0.18"),
        week52_high=D("1611"), week52_low=D("1253"),
    )


def test_explorer_returns_a_structured_read():
    model = FakeModel("claude", [{
        "valuation": "fair", "profitability": "modest", "leverage": "manageable",
        "momentum": "mid-range", "overall": "steady large cap",
    }])
    read, err = explorer.run(_fundamentals(), _headlines(), model)
    assert err is None
    assert read.overall == "steady large cap"


def test_explorer_reports_failure_without_raising():
    from app.llm.base import AnalystError
    model = FakeModel("groq", [AnalystError("x"), AnalystError("x")])
    read, err = explorer.run(_fundamentals(), _headlines(), model)
    assert read is None and err


# --- health report ------------------------------------------------------------------


def test_report_deterministic_flags_present_without_a_model():
    v = view([stock("BIG", "IT", 75, invested="75000"), stock("REST", "Auto", 25, invested="25000")],
             [sector("IT", 75), sector("Auto", 25)], invested="100000")
    score, narrative, flags = health_report.generate(v, [], None)  # no model
    texts = [f["text"] for f in flags]
    # The report flags the single most concentrated stock (BIG at 75%), always computed.
    assert any("BIG is 75" in t for t in texts)
    assert isinstance(score, int) and 0 <= score <= 100
    assert narrative  # rules narrative fills in


def test_report_model_score_and_extra_flags_merge():
    v = view([stock("A", "IT", 50), stock("B", "Auto", 50)], [sector("IT", 50), sector("Auto", 50)])
    model = FakeModel("claude", [{
        "health_score": 64, "narrative": "Reasonably balanced.",
        "extra_flags": [{"ok": False, "text": "Two-stock book is thin"}],
    }])
    score, narrative, flags = health_report.generate(v, [], model)
    assert score == 64
    assert any("thin" in f["text"] for f in flags)  # model flag added
    assert narrative == "Reasonably balanced."


def test_report_model_failure_falls_back_to_deterministic():
    from app.llm.base import AnalystError
    v = view([stock("BIG", "IT", 30, invested="30000"), stock("R", "Auto", 70, invested="70000")],
             [sector("IT", 30), sector("Auto", 70)], invested="100000")
    model = FakeModel("groq", [AnalystError("x"), AnalystError("x")])
    score, narrative, flags = health_report.generate(v, [], model)
    assert flags and narrative  # deterministic report still produced
