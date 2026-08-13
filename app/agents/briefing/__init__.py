"""The hierarchical briefing (phase 3).

An orchestrator agent over per-market agents over per-sector agents, wired with LangGraph.
The nodes are plain functions that call the existing `AnalystModel` seam, so no LangChain
model packages are involved and the whole graph is testable offline with `FakeModel`.

Public entry point is `graph.run_briefing(...)`; the reasoning lives in the pure functions
`sector_agent.analyze`, `market_agent.synthesize`, and `orchestrator.synthesize`, which the
graph nodes wrap. See `app/agents/README.md`.
"""
