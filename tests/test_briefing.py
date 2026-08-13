"""The phase-3 hierarchical briefing: the three agents, and the LangGraph that wires them.

All offline — no key, no network. The sector agents fan out in parallel threads inside the
graph, so a queued-in-order FakeModel can't be used there (replies would pop in a
non-deterministic order); the graph tests use a model that routes by the system prompt
instead. The single-call agent functions use the ordinary FakeModel.
"""

from __future__ import annotations

import pytest

from app.agents.briefing import market_agent, orchestrator, sector_agent
from app.agents.briefing.graph import run_briefing
from app.llm.base import AnalystError
from tests.conftest import FakeModel


# --- fakes ---------------------------------------------------------------------------


class NoNews:
    def get_news(self, market, tickers, *, per_ticker=3, within_days=7):
        return []


class NoFund:
    def get_fundamentals(self, market, ticker):
        return None


class RoutingModel:
    """Returns a shape based on WHICH agent is calling (detected from the system prompt),
    so it is order-independent under the graph's parallel fan-out. Optionally raises for a
    named sector, to exercise partial failure."""

    name = "groq"

    def __init__(self, fail_sector: str | None = None):
        self.fail_sector = fail_sector
        self.calls = 0

    def complete_json(self, system: str, user: str, *, max_tokens=1500) -> dict:
        self.calls += 1
        if "specialises in" in system:  # sector agent
            if self.fail_sector and f"the {self.fail_sector} sector" in system:
                raise AnalystError("sector model down")
            return {
                "direction": "positive", "significance": "high",
                "what_happened": "Sector rallied on strong results.",
                "what_it_indicates": "Momentum is favourable.", "tickers": ["AAA"],
            }
        if "market strategist" in system:  # market agent
            return {"overall_direction": "positive", "summary": "Broadly up.",
                    "cross_sector_notes": "IT and Financials both up on the rate cut."}
        return {  # orchestrator
            "headline": "A good week", "overall_direction": "positive",
            "summary": "Both markets rose.", "positives": ["IT up"], "negatives": [],
        }


def _task(market, sector, ticker="AAA", alloc=12.0, pnl=8.0, cap="large"):
    return {
        "market": market, "currency": "INR" if market == "INDIA" else "USD",
        "sector": sector,
        "holdings": [{"ticker": ticker, "name": f"{ticker} Ltd",
                      "allocation_pct": alloc, "pnl_pct": pnl, "cap_class": cap}],
    }


# --- sector agent --------------------------------------------------------------------


def test_sector_agent_llm_reply_filters_to_held():
    model = FakeModel("groq", [{
        "direction": "negative", "significance": "medium",
        "what_happened": "Margins compressed.", "what_it_indicates": "Watch guidance.",
        "tickers": ["AAA", "NOTHELD"],
    }])
    finding = sector_agent.analyze(_task("INDIA", "IT"), model, None, None)
    assert finding.direction == "negative"
    assert finding.significance == "medium"
    assert finding.tickers == ["AAA"]  # NOTHELD dropped — not in the sector's holdings


def test_sector_agent_deterministic_without_a_model():
    finding = sector_agent.analyze(_task("INDIA", "IT", pnl=8.0), None, None, None)
    assert finding.direction == "positive"  # +8% P/L
    assert "position P/L" in finding.what_it_indicates


def test_sector_agent_negative_direction_from_loss():
    finding = sector_agent.analyze(_task("INDIA", "IT", pnl=-9.0), None, None, None)
    assert finding.direction == "negative"


def test_sector_agent_model_failure_falls_back_not_raises():
    model = FakeModel("groq", [AnalystError("x"), AnalystError("x")])
    finding = sector_agent.analyze(_task("INDIA", "IT", pnl=3.0), model, None, None)
    assert finding.direction == "positive"  # deterministic from +3%
    assert "position P/L" in finding.what_it_indicates


def test_sector_agent_unpriced_is_neutral():
    task = _task("INDIA", "IT")
    task["holdings"][0]["pnl_pct"] = None
    finding = sector_agent.analyze(task, None, None, None)
    assert finding.direction == "neutral"
    assert "could not be priced" in finding.what_happened


def test_sector_agent_coerces_a_bad_direction():
    model = FakeModel("groq", [{"direction": "bullish", "significance": "huge",
                                "what_happened": "x", "what_it_indicates": "y"}])
    finding = sector_agent.analyze(_task("INDIA", "IT"), model, None, None)
    assert finding.direction == "neutral"      # "bullish" is not in the vocabulary
    assert finding.significance == "medium"     # "huge" coerced too


# --- market agent --------------------------------------------------------------------


def _finding(sector, direction, significance="high", market="INDIA"):
    return {"market": market, "sector": sector, "direction": direction,
            "significance": significance, "what_happened": "w", "what_it_indicates": "i",
            "tickers": ["AAA"]}


def test_market_agent_synthesizes_and_carries_sectors():
    model = FakeModel("groq", [{"overall_direction": "positive", "summary": "Up.",
                                "cross_sector_notes": "Shared rate driver."}])
    brief = market_agent.synthesize("INDIA", "INR",
                                    [_finding("IT", "positive"), _finding("Energy", "negative")], model)
    assert brief.overall_direction == "positive"
    assert brief.cross_sector_notes == "Shared rate driver."
    assert [s.sector for s in brief.sectors] == ["IT", "Energy"]  # carried through


def test_market_agent_deterministic_tally():
    # Two positive (high=3 each) vs one negative (low=1) -> net positive.
    brief = market_agent.synthesize("INDIA", "INR", [
        _finding("IT", "positive", "high"),
        _finding("FMCG", "positive", "high"),
        _finding("Energy", "negative", "low"),
    ], None)
    assert brief.overall_direction == "positive"
    assert brief.cross_sector_notes == ""


# --- orchestrator --------------------------------------------------------------------


def _brief(market, direction, findings):
    return {"market": market, "overall_direction": direction, "summary": "s",
            "cross_sector_notes": "", "sectors": findings}


def test_orchestrator_synthesizes_the_deliverable():
    model = FakeModel("claude", [{
        "headline": "Good week", "overall_direction": "positive", "summary": "Up.",
        "positives": ["IT rallied"], "negatives": ["Energy slipped"],
    }])
    briefing = orchestrator.synthesize(
        [_brief("INDIA", "positive", [_finding("IT", "positive")])], {}, model)
    assert briefing.headline == "Good week"
    assert briefing.positives == ["IT rallied"]
    assert briefing.generated_by == "claude"


def test_orchestrator_deterministic_splits_findings():
    briefing = orchestrator.synthesize([
        _brief("INDIA", "positive", [_finding("IT", "positive"), _finding("Energy", "negative")]),
    ], {}, None)
    assert briefing.generated_by == "rules"
    assert any("IT" in p for p in briefing.positives)
    assert any("Energy" in n for n in briefing.negatives)


# --- the graph -----------------------------------------------------------------------


def test_graph_fans_out_over_markets_and_sectors():
    tasks = [
        _task("INDIA", "IT"), _task("INDIA", "Financial services"),
        _task("US", "Information Technology"),
    ]
    ctx = {"INDIA": {"currency": "INR", "pnl_pct": 4.0}, "US": {"currency": "USD", "pnl_pct": 5.0}}
    model = RoutingModel()
    b = run_briefing(user_id="local", plan="free", sector_tasks=tasks, market_ctx=ctx,
                     model=model, news=NoNews(), fundamentals=NoFund())
    # 3 sectors + 2 markets + 1 orchestrator.
    assert model.calls == 6
    assert {m["market"] for m in b["markets"]} == {"INDIA", "US"}
    india = next(m for m in b["markets"] if m["market"] == "INDIA")
    assert {s["sector"] for s in india["sectors"]} == {"IT", "Financial services"}
    assert b["overall_direction"] == "positive"


def test_graph_empty_portfolio_short_circuits():
    b = run_briefing(user_id="local", plan="free", sector_tasks=[], market_ctx={}, model=None)
    assert b["markets"] == []
    assert "Nothing to brief" in b["headline"]


def test_graph_partial_failure_degrades_one_sector():
    tasks = [_task("INDIA", "IT", pnl=6.0), _task("INDIA", "Energy", pnl=-7.0)]
    ctx = {"INDIA": {"currency": "INR", "pnl_pct": 0.0}}
    model = RoutingModel(fail_sector="Energy")  # Energy's sector agent throws
    b = run_briefing(user_id="local", plan="free", sector_tasks=tasks, market_ctx=ctx,
                     model=model, news=NoNews(), fundamentals=NoFund())
    india = b["markets"][0]
    energy = next(s for s in india["sectors"] if s["sector"] == "Energy")
    it = next(s for s in india["sectors"] if s["sector"] == "IT")
    # Energy fell back to its deterministic P/L reading; IT used the model.
    assert energy["direction"] == "negative"
    assert "position P/L" in energy["what_it_indicates"]
    assert it["direction"] == "positive"


def test_graph_no_model_is_fully_deterministic():
    tasks = [_task("INDIA", "IT", pnl=10.0), _task("US", "Information Technology", pnl=-4.0)]
    ctx = {"INDIA": {"currency": "INR", "pnl_pct": 6.0}, "US": {"currency": "USD", "pnl_pct": -2.0}}
    b = run_briefing(user_id="local", plan="free", sector_tasks=tasks, market_ctx=ctx, model=None)
    assert b["generated_by"] == "rules"
    assert any("IT (INDIA)" in p for p in b["positives"])
    assert any("Information Technology (US)" in n for n in b["negatives"])
