# Portfolio Manager

Upload your holdings as a spreadsheet, get a live dashboard: per-stock and per-sector
allocation, market value and profit/loss, for an **India (NSE)** and a **US** portfolio
side by side.

Built from `Portfolio Management.docx`. Phase 1 is the dashboard; the architecture is
shaped so the **smart agentic layer** is additive — see [`app/agents/README.md`](app/agents/README.md).

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
docker compose up -d                 # Postgres on :5433
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.api.main:app --reload
```

Open <http://localhost:8000>, download the **filled sample** from the bottom of the page,
upload it, and hit **Refresh prices**.

```bash
.venv/bin/python -m pytest            # 62 tests; needs the Postgres container up
```

## How it works

**Excel in, website out.** The template has an `India_Holdings` sheet, a `US_Holdings`
sheet and a `Deletions` sheet. Sector is a real Excel dropdown constrained to that
market's taxonomy, so allocation is deterministic rather than at the mercy of whatever a
data provider reports.

- **Bulk upload** (_Replace portfolio_) wipes and reloads — but only the markets the file
  actually has rows for. An India-only re-upload will not silently wipe your US holdings.
- **Incremental upload** (_Add to portfolio_) appends. Buying the same stock twice at
  different prices creates two lots.
- **Incremental delete** removes units, consuming the **oldest lot first (FIFO)**, which
  matches tax treatment in both countries. Fewer units than you hold shrinks the position;
  all of them drops the stock.

Uploads are **validate-then-commit**: every bad cell in the file is reported at once and
_nothing_ is written. A half-applied portfolio upload is worse than a rejected one.

### Three things worth knowing

**Allocation % is computed on invested amount, never market value.** The doc says so twice
and it is the single easiest thing to get wrong. A useful consequence: allocation stays
correct even if every price fetch fails, because it never touches a price.

**Money is `Decimal` and `NUMERIC` end to end — never a float.** `0.1` has no exact binary
representation and the error compounds through `units × price → invested → allocation %`.
Floats are stopped at all three boundaries: reading the spreadsheet, reading Yahoo (which
returns ₹1296.80 as `1296.800048828125`), and serialising JSON (where money crosses the
wire as a **string**, because a JSON number is an IEEE double).

**A dead ticker degrades, it doesn't break.** If a price can't be fetched the row shows a
blank market value and the stock is listed as unpriced — never a fabricated number. If a
price was fetched before, the last known one is served and flagged _stale_. This is not
hypothetical: `TATAMOTORS` now 404s on Yahoo, because Tata Motors demerged in 2025 and the
passenger-vehicle entity trades as `TMPV`.

## Architecture

```
core/       pure domain — Decimal maths, no I/O, no database   ← agents reuse this
ingest/     Excel template generator + validating reader
market/     MarketDataProvider protocol → YFinanceProvider, TTL price cache
store/      Postgres schema + repositories (the only place with SQL)
services/   ← THE SEAM: upload, delete, build_dashboard(market)
api/        FastAPI routes + vanilla JS/SVG frontend (no build step, no Node)
exporters/  the dashboard as .xlsx with charts (the doc's original ask)
jobs/       daily refresh + snapshot
agents/     RESERVED — phase 2
```

`core` knows nothing about I/O. `services` orchestrate `store` + `market` + `core`.
`api`, `exporters`, `jobs` and — later — `agents` are all _peers_ that consume `services`.
That is what makes the agent layer a plug-in rather than a rewrite.

The dashboard maths lives in `core` as pure functions rather than a SQL `GROUP BY`,
specifically so an agent can run it over a _hypothetical_ portfolio — "what happens to my
sector balance if I exit this position?" — which a query against stored rows cannot answer.

## Deploy free

| Piece           | Service            | Notes                                                    |
| --------------- | ------------------ | -------------------------------------------------------- |
| Database + Auth | **Supabase**       | 500 MB Postgres; thousands of rows, nowhere near the cap |
| Web app         | **Render**         | free web service, deploys the Dockerfile from GitHub     |
| Daily refresh   | **GitHub Actions** | Render's own cron is paid; a scheduled workflow is free  |

1. **Supabase** → new project. Copy the **connection pooler** URI (Settings → Database,
   port 6543) and change the scheme to `postgresql+psycopg://`. Grab the anon key and the
   JWT secret from Settings → API. Create your login under Authentication → Users.
2. **Render** → New Web Service → point at this repo. `render.yaml` defines it; fill in
   `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` and a random
   `REFRESH_TOKEN`. Migrations run on boot.
3. **GitHub** → repo secrets `APP_URL` (your Render URL) and `REFRESH_TOKEN` (the same
   value). The workflow in `.github/workflows/daily.yml` does the rest.

Two things to know about the free tier, stated plainly:

- **Cold starts.** Render sleeps the service after ~15 minutes idle; the next request takes
  30–60 seconds. Fine for a dashboard you open a few times a day. The daily workflow wakes
  it with a health check before refreshing, so the cron never times out on a cold start.
- **The daily job is load-bearing.** Supabase pauses a free project after 7 days of
  inactivity. The refresh touching the database daily is what keeps it alive — if you
  disable that workflow, the database eventually goes to sleep.

Local development points at the Docker Postgres, not Supabase, so tests are hermetic,
offline, and cannot corrupt real holdings. `DATABASE_URL` is the only difference.
