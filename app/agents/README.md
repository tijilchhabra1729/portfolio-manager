# The agent layer

Phase 1 left this reserved; phase 2 built it. The contract it was designed around held:
every agent reads through `services/` and writes via `repository` / `agent_repo`, and
**none of it touches the frontend** — insights surface through the endpoint and panel that
already existed.

## What's here

**Rule agents** — pure functions over a `DashboardView`, free, deterministic, no
hallucination risk:

| Agent | Fires on |
|---|---|
| `concentration.py` | a stock > 10%/20% or a sector > 25%/40% of invested |
| `small_cap.py` | small caps > 5% (the doc's rule) / 10%, using `StockRow.cap_class` |
| `drift.py` | a sector's allocation moved ≥ 5 points vs a ~week-ago snapshot |

**LLM agents** — one bounded JSON call each, behind the `AnalystModel` protocol:

| Agent | Does |
|---|---|
| `analyst.py` | maps recent headlines to specific holdings → news insights |
| `explorer.py` | reads one ticker's fundamental ratios (the Explore tab) |
| `health_report.py` | a weekly score + narrative + red-flag checklist |

**`runner.py`** publishes with dedupe: rule insights are *reconciled* (upsert what's true,
delete what cleared, keep dismissed rows dismissed); analyst insights are *aged out* after
a week. This is what stops a nightly rerun from stacking duplicates.

## The two-provider seam

`app/llm/` mirrors `app/market/base.py`: a `complete_json` protocol with two
implementations — `openai_compat.py` (Groq, the free tier) and `claude.py` (premium).
`select.py` picks per the user's plan and which keys exist. No key → the rule agents still
run and the LLM tabs say "set a key". Adding a third provider is one class.

## Where the maths still pays off

`core/calculations.py` stays pure, so an agent can run the real allocation maths over a
*hypothetical* portfolio — the phase-1 promise, now used by `health_report` and available
to any future rebalancing agent. Company-size classification lives in `core/market_cap.py`
for the same reason: `small_cap.py`, the Stocks table, and the report all read one answer.

## Phase 3: the hierarchical briefing (`briefing/`)

A second, deeper agent shape sits alongside the flat phase-2 agents: a three-level
**orchestrator → market → sector** hierarchy, wired with **LangGraph**, that produces the
weekly **Briefing** — for each development, *what happened*, *what it indicates*, and a
positive / negative / neutral **direction** (the phase-2 insights only ever warn).

| Level | File | Does |
|---|---|---|
| Sector | `briefing/sector_agent.py` | one specialist per held sector — pulls its own news + ratios, reasons through a sector-specific lens (`briefing/lenses.py`) → a `SectorFinding` |
| Market | `briefing/market_agent.py` | reads its sectors' findings and reasons *across* them (shared drivers) → a `MarketBrief` |
| Orchestrator | `briefing/orchestrator.py` | reads both market briefs, reasons across markets → the `Briefing` (headline, direction, positives, negatives) |

**LangGraph, not LangChain.** The graph (`briefing/graph.py`, the only LangGraph-aware
file) is two `Send` fan-out layers — one per held sector, then one per market — joined by a
`gather` node; the parallel branches append to `operator.add` state channels. But the nodes
are plain functions that call our own `AnalystModel.complete_json` seam, so no LangChain
model packages are pulled in and the whole graph is testable offline with `FakeModel`. The
reasoning lives in the pure `analyze` / `synthesize` functions; `graph.py` only wires them.

**Fan-out is bounded** (top-N held sectors per market) because a full run is one LLM call
per sector + per market + the orchestrator. It runs **weekly** (the Monday cron) and
**on-demand** (cooldown-guarded), never nightly. Every node degrades to a deterministic
reading (direction from P/L) if there's no model or a call fails, so one rate-limited or
failed agent costs its own card, never the briefing — the same stale-price philosophy.
Groq's free tier is TPM-capped, so `OpenAICompatModel` honours a 429 `Retry-After` with a
bounded backoff before falling back.

## What's still open (phase 4 candidates)

Rebalancing *recommendations* ("sell X, buy Y"), backtesting, editable thresholds, and a
bidirectional clarification loop (the orchestrator re-dispatching a sector with a follow-up
question — LangGraph makes this a natural extension of the existing graph). The scheduler
already exists — `/api/refresh` runs the rule + analyst agents nightly, the health report
and the hierarchical briefing weekly (Monday), per user.
