"""The LangGraph wiring — the only LangGraph-aware file in the app.

A three-level fan-out/fan-in over the pure reasoning functions in this package. LangGraph's
nodes are plain callables, so they call our own `AnalystModel` seam directly (no LangChain
model wrapper) and the model + providers are injected per-invocation through the run config,
leaving the compiled graph itself stateless and reusable across users and plans.

Topology (verified against langgraph 1.2.x):

    START --Send(one per held sector)--> sector_agent  (parallel, bounded)
    sector_agent --edge--> gather        (runs once, after every sector)
    gather --Send(one per market)--> market_agent      (parallel)
    market_agent --edge--> orchestrator  (runs once, after both markets)
    orchestrator --> END

The two `Annotated[list, operator.add]` channels in `BriefingState` are what let the
parallel branches append without clobbering; a plain edge from a fanned-out node runs the
downstream node exactly once (LangGraph's map-reduce semantic).
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

# We never want telemetry leaving the box. There is no LangSmith key here anyway, but be
# explicit rather than rely on that.
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.briefing import market_agent, orchestrator, sector_agent
from app.agents.briefing.state import BriefingState
from app.llm.base import AnalystModel
from app.market.fundamentals import FundamentalsProvider, YFinanceFundamentals
from app.market.news import NewsProvider, YFinanceNewsProvider

log = logging.getLogger(__name__)

_default_news = YFinanceNewsProvider()
_default_fundamentals = YFinanceFundamentals()


# --- nodes (thin adapters over the pure functions) ---------------------------------


def _sector_node(payload: dict, config) -> dict:
    deps = config["configurable"]
    finding = sector_agent.analyze(payload, deps["model"], deps["news"], deps["fundamentals"])
    return {"sector_findings": [finding.model_dump()]}


def _gather(state: BriefingState) -> dict:
    return {}  # convergence point only: one place to fan out to markets from


def _market_node(payload: dict, config) -> dict:
    deps = config["configurable"]
    brief = market_agent.synthesize(
        payload["market"], payload["currency"], payload["findings"], deps["model"]
    )
    return {"market_briefs": [brief.model_dump()]}


def _orchestrator_node(state: BriefingState, config) -> dict:
    deps = config["configurable"]
    briefing = orchestrator.synthesize(
        state.get("market_briefs", []), state.get("market_ctx", {}), deps["model"]
    )
    return {"briefing": briefing.model_dump()}


# --- routing (the fan-out functions) ------------------------------------------------


def _route_sectors(state: BriefingState) -> list[Send]:
    return [Send("sector_agent", task) for task in state.get("sector_tasks", [])]


def _route_markets(state: BriefingState) -> list[Send]:
    by_market: dict[str, list[dict]] = defaultdict(list)
    for f in state.get("sector_findings", []):
        by_market[f["market"]].append(f)
    ctx = state.get("market_ctx", {})
    return [
        Send(
            "market_agent",
            {"market": m, "currency": ctx.get(m, {}).get("currency", ""), "findings": findings},
        )
        for m, findings in by_market.items()
    ]


def build_graph():
    g = StateGraph(BriefingState)
    g.add_node("sector_agent", _sector_node)
    g.add_node("gather", _gather)
    g.add_node("market_agent", _market_node)
    g.add_node("orchestrator", _orchestrator_node)
    g.add_conditional_edges(START, _route_sectors, ["sector_agent"])
    g.add_edge("sector_agent", "gather")
    g.add_conditional_edges("gather", _route_markets, ["market_agent"])
    g.add_edge("market_agent", "orchestrator")
    g.add_edge("orchestrator", END)
    return g.compile()


# Compiled once; stateless — the model and providers arrive per-invocation via config.
_graph = build_graph()


def run_briefing(
    *,
    user_id: str,
    plan: str,
    sector_tasks: list[dict],
    market_ctx: dict,
    model: AnalystModel | None,
    news: NewsProvider | None = None,
    fundamentals: FundamentalsProvider | None = None,
    max_concurrency: int = 3,
) -> dict:
    """Run the graph and return the briefing as a dict (ready for the DB / the wire).

    With no held sectors the fan-out would be empty and the graph would dead-end, so we
    short-circuit to the orchestrator's empty-portfolio briefing directly.
    """
    if not sector_tasks:
        return orchestrator.synthesize([], market_ctx, model).model_dump()

    init: BriefingState = {
        "user_id": user_id,
        "plan": plan,
        "sector_tasks": sector_tasks,
        "market_ctx": market_ctx,
        "sector_findings": [],
        "market_briefs": [],
    }
    config = {
        "max_concurrency": max_concurrency,
        "configurable": {
            "model": model,
            "news": news or _default_news,
            "fundamentals": fundamentals or _default_fundamentals,
        },
    }
    out = _graph.invoke(init, config=config)
    return out["briefing"]
