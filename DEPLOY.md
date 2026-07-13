# Deploying

Six steps. Everything is free tier, no card required.

You do the browser steps; the shell steps are copy-paste. **Step 4 is the one that
catches people** — read the two warnings before you paste the connection string.

---

## 1. Git

```bash
cd ~/Desktop/portfolio-manager

git init
git add -A

# Confirm no secrets are staged. Both of these must print NOTHING.
git diff --cached --name-only | grep -x ".env"
git diff --cached -U0 | grep -iE "eyJ[A-Za-z0-9]{10,}|service_role"

git commit -m "Portfolio dashboard: Excel intake, India + US, live prices"
```

`.env` and `.venv/` are gitignored. The grep above is belt-and-braces — if either line
prints anything, stop and tell me.

## 2. GitHub

Create an **empty private repo** at <https://github.com/new> (no README, no .gitignore —
you already have both). Then:

```bash
git remote add origin https://github.com/<you>/portfolio-manager.git
git branch -M main
git push -u origin main
```

## 3. Supabase project

<https://supabase.com/dashboard> → **New project**. Pick a region near you (`ap-south-1`
for India). Save the database password it generates — you need it in the next step and
Supabase will not show it again.

Wait for it to finish provisioning (~2 min).

## 4. The connection string ← the step that goes wrong

**Settings → Database → Connection pooling.** Copy the **Session mode** URI.

Two edits are mandatory:

```
# What Supabase gives you:
postgresql://postgres.abcdefgh:YOUR-PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres

# What you need:
postgresql+psycopg://postgres.abcdefgh:YOUR-PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
          ^^^^^^^^ add this, or SQLAlchemy loads the wrong driver
```

> **Do NOT use the "Direct connection" string** (`db.<ref>.supabase.co`). Supabase serves
> it over **IPv6 only**, and Render has no IPv6 egress. It will work perfectly from your
> laptop and then fail in production with an unhelpful timeout. Use the **pooler** host —
> it's the one with `pooler.supabase.com` in it.

> **Percent-encode your password** if it contains `@ # ? / :` — e.g. `@` becomes `%40`.
> Otherwise the URI parses wrong and you get a baffling auth error.

Then, from **Settings → API**, grab:

- **Project URL** → `SUPABASE_URL`
- **anon public** key → `SUPABASE_ANON_KEY` (public by design; it ships to the browser)
- **JWT Secret** → `SUPABASE_JWT_SECRET` (secret — never sent to the browser)

And create your login: **Authentication → Users → Add user** (email + password, tick
auto-confirm). The app has no signup page on purpose.

## 5. Migrate, then prove the data is private

Point your **local** `.env` at Supabase temporarily and run the migrations from your
laptop — this creates the tables and, crucially, locks them away from Supabase's public
REST API.

```bash
# In .env, set DATABASE_URL to the pooler URI from step 4, plus:
#   AUTH_ENABLED=true
#   SUPABASE_URL=...
#   SUPABASE_ANON_KEY=...
#   SUPABASE_JWT_SECRET=...
#   REFRESH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.verify_supabase
```

`verify_supabase` must print **`All checks passed`**. The check that matters is
**"Anon-key read attempt"**: it takes the public anon key and tries to read your holdings
through `https://<project>.supabase.co/rest/v1/transactions` exactly as an attacker with
your page source would. It has to come back **denied**.

Why this exists: Supabase auto-exposes every table in `public` via PostgREST, and its
default grants let the `anon` role read them. Migration `9b1c4d7e2a01` revokes those
grants and enables row-level security, so the browser key can't touch your portfolio.
**If you skip step 5, your holdings are world-readable.**

When you're done, **put `.env` back to the local Docker Postgres** so tests stay hermetic:

```bash
# DATABASE_URL=postgresql+psycopg://pm:pm@localhost:5433/portfolio
# AUTH_ENABLED=false
```

## 6. Render + the cron

<https://dashboard.render.com> → **New → Blueprint** → pick the repo. It reads
`render.yaml`. Fill in the five secrets it asks for (same values as step 5, and the same
`REFRESH_TOKEN`).

Migrations run on container boot, so the deploy is self-applying.

Then in **GitHub → repo → Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `APP_URL` | your Render URL, e.g. `https://portfolio-manager-xxxx.onrender.com` (no trailing slash) |
| `REFRESH_TOKEN` | the same token you gave Render |

Trigger it once by hand: **Actions → Daily refresh → Run workflow**.

---

## Check it worked

```bash
curl https://<your-app>.onrender.com/healthz          # {"ok":true} (may take 60s — cold start)
curl https://<your-app>.onrender.com/api/INDIA/dashboard   # 401: auth is on. Good.
```

Open the URL, sign in with the user from step 4, download the **filled sample** from the
bottom of the page, upload it, hit **Refresh prices**.

## Two things to keep in mind

**Cold starts.** Render sleeps the service after ~15 min idle; the next request takes
30–60 s. The daily workflow health-checks the app before refreshing, so the cron never
times out on a sleeping service.

**The daily cron is load-bearing, not just a feature.** Supabase pauses a free project
after **7 days of inactivity**. The nightly refresh touching the database is what keeps it
awake. If you ever disable that workflow, the database will eventually go to sleep and the
app will start throwing connection errors.
