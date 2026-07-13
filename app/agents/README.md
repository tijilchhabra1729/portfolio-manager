# The agent layer

**Empty on purpose.** Phase 1 built the dashboard; this is where phase 2 goes. Everything
an agent needs already exists and is tested — this file is the contract, so that adding
the first agent is writing one file, not refactoring the application.

## The contract

An agent is a peer of `api/`, `exporters/` and `jobs/`. It **reads through `services/`**
and **writes rows to `insights`**. It does not touch SQL, and it does not touch the
frontend.

```python
from app.core.sectors import Market
from app.services import dashboard_service
from app.store import repository
from app.store.db import connect

def run(user_id: str, market: Market) -> None:
    with connect() as conn:
        view = dashboard_service.build(conn, user_id, market)

        for sector in view.sectors:
            if sector.allocation_pct > 25:
                repository.add_insight(
                    conn, user_id, market,
                    severity="warning",
                    title=f"{sector.sector} is {sector.allocation_pct}% of the portfolio",
                    body="Above the 25% single-sector guideline. Consider trimming.",
                    source="concentration-agent",
                    related_sector=sector.sector,
                )
```

That is the whole integration. The insight appears in the dashboard's Insights panel on
the next page load — **no API change, no frontend change.** `GET /api/{market}/insights`
and the panel that renders it were both built and tested in phase 1 against exactly this.

## What phase 1 left you

| You need | It is already there |
|---|---|
| Current holdings, allocation, P/L | `dashboard_service.build(conn, user_id, market) -> DashboardView` |
| **Allocation drift over time** | `portfolio_snapshots` — written daily by `jobs/daily_refresh.py`, with `sector_allocations` as `{sector: pct}` so you can diff two dates without replaying the ledger |
| Price history | `price_snapshots` — one row per ticker per day |
| Full transaction history | `repository.get_transactions()` — an append-only ledger, never mutated |
| Market cap (for the "small caps < 5%" rule) | `price_snapshots.market_cap`, already being captured |
| A place to publish findings | `repository.add_insight()` + a live endpoint + a UI panel |

**The snapshots are the thing to understand.** They are collected from day one *for you* —
"has my IT exposure drifted?" is unanswerable without a time series, and a day that goes
uncaptured cannot be recovered later. By the time the first agent runs, there should
already be months of history sitting in `portfolio_snapshots`.

## Why the maths is pure Python and not SQL

`core/calculations.py` has no I/O. `build_dashboard()` takes positions, instruments and
quotes as plain arguments, which means an agent can run the **real** allocation maths over
a **hypothetical** portfolio:

```python
from app.core.calculations import build_dashboard, build_positions

# "What happens to my sector balance if I exit Infosys?"
hypothetical = {t: p for t, p in positions.items() if t != "INFY"}
after = build_dashboard(market, hypothetical, instruments, quotes)
```

Computing allocation with a `GROUP BY` would have been simpler and would have made this
impossible. It was written this way for this.

## Adding a new data source

Agents will want news, filings, fundamentals. Follow `market/base.py`: define a Protocol,
implement it, and inject it — the same shape that lets `YFinanceProvider` be swapped for a
paid feed without the dashboard noticing.

## Where a scheduler goes

`jobs/daily_refresh.py` is the model. Add `jobs/run_agents.py`, and extend
`.github/workflows/daily.yml` to call it after the refresh — the snapshot must be written
before the agents read it.
