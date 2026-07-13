"""Check a Supabase setup end to end, and prove the data is actually private.

    .venv/bin/python -m scripts.verify_supabase

Reads .env. Every check either PASSes with evidence or FAILs with the fix. The last one
matters most: it takes the public anon key and tries to read your holdings through
Supabase's REST API the way an attacker would. It must come back denied.
"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from app.config import settings
from app.store.db import connect, engine
from app.store.tables import metadata

OK, BAD, WARN = "  PASS  ", "  FAIL  ", "  WARN  "
failures: list[str] = []


def report(status: str, title: str, detail: str = "") -> None:
    print(f"[{status}] {title}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"         {line}")
    if status == BAD:
        failures.append(title)


def check_url() -> None:
    url = settings().database_url
    if not url.startswith("postgresql+psycopg://"):
        report(
            BAD,
            "DATABASE_URL driver",
            "Must start with postgresql+psycopg:// -- Supabase gives you a bare\n"
            "postgresql:// URI, so change the scheme or SQLAlchemy picks the wrong driver.",
        )
        return
    report(OK, "DATABASE_URL driver", "postgresql+psycopg")

    host = urlsplit(url.replace("postgresql+psycopg", "https")).hostname or ""

    if host.startswith("db.") and host.endswith(".supabase.co"):
        report(
            BAD,
            "DATABASE_URL host",
            f"{host} is the DIRECT connection, which Supabase now serves over IPv6 only.\n"
            "Render's free tier has no IPv6 egress, so this will work from your laptop\n"
            "and then fail in production with a confusing timeout.\n"
            "Use the POOLER host instead: Settings -> Database -> Connection pooling.",
        )
    elif "pooler.supabase.com" in host:
        mode = "transaction (6543)" if ":6543" in url else "session (5432)"
        report(OK, "DATABASE_URL host", f"pooler, {mode} mode -- reachable over IPv4")
    elif "localhost" in host or "127.0.0.1" in host:
        report(WARN, "DATABASE_URL host", "pointing at local Postgres, not Supabase")
    else:
        report(WARN, "DATABASE_URL host", host)

    if "@" in url and any(c in url.split("@")[0] for c in "?#[]"):
        report(
            WARN,
            "Password encoding",
            "Password contains characters that must be percent-encoded in a URI\n"
            "(@ -> %40, # -> %23, ? -> %3F). Otherwise the URL parses wrong.",
        )


def check_connection() -> bool:
    try:
        with connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            who = conn.execute(text("SELECT current_user")).scalar_one()
    except Exception as exc:
        report(BAD, "Database connection", str(exc).splitlines()[0])
        return False
    report(OK, "Database connection", f"{version.split(',')[0]}\nconnected as: {who}")
    return True


def check_schema() -> None:
    expected = set(metadata.tables)
    with connect() as conn:
        found = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            )
        }
    missing = expected - found
    if missing:
        report(
            BAD,
            "Schema",
            f"Missing tables: {', '.join(sorted(missing))}\nRun: alembic upgrade head",
        )
    else:
        report(OK, "Schema", f"{len(expected)} tables present")


def check_rls() -> None:
    """RLS on, and no grants to the roles the browser can act as."""
    with connect() as conn:
        unprotected = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND c.relname = ANY(:tables) AND NOT c.relrowsecurity
                    """
                ),
                {"tables": list(metadata.tables)},
            )
        ]
        granted = [
            f"{r[0]} -> {r[1]}"
            for r in conn.execute(
                text(
                    """
                    SELECT table_name, grantee FROM information_schema.role_table_grants
                    WHERE table_schema = 'public'
                      AND grantee IN ('anon', 'authenticated')
                    """
                )
            )
        ]

    if unprotected:
        report(BAD, "Row-level security", f"RLS OFF on: {', '.join(unprotected)}")
    else:
        report(OK, "Row-level security", "enabled on every table")

    if granted:
        report(
            BAD,
            "PostgREST grants",
            "anon/authenticated still hold grants -- your holdings are readable with\n"
            "the public anon key:\n" + "\n".join(f"  {g}" for g in granted),
        )
    else:
        report(OK, "PostgREST grants", "anon and authenticated have no table privileges")


def check_the_actual_attack() -> None:
    """The one that counts. Use the public anon key exactly as an attacker would."""
    url, key = settings().supabase_url.rstrip("/"), settings().supabase_anon_key
    if not url or not key:
        report(WARN, "Anon-key read attempt", "SUPABASE_URL / SUPABASE_ANON_KEY not set")
        return

    try:
        res = httpx.get(
            f"{url}/rest/v1/transactions",
            params={"select": "*", "limit": "1"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=20,
        )
    except Exception as exc:
        report(WARN, "Anon-key read attempt", f"could not reach PostgREST: {exc}")
        return

    leaked = res.status_code == 200 and res.json()
    if leaked:
        report(
            BAD,
            "Anon-key read attempt",
            "YOUR HOLDINGS ARE PUBLIC. The anon key read the transactions table over\n"
            f"REST ({res.status_code}). This key ships to every browser that loads the\n"
            "dashboard. Run: alembic upgrade head",
        )
    else:
        report(
            OK,
            "Anon-key read attempt",
            f"denied ({res.status_code}) -- the key that ships to the browser cannot\n"
            "read your portfolio, which is the point",
        )


def check_auth() -> None:
    if not settings().auth_enabled:
        report(WARN, "Auth", "AUTH_ENABLED=false -- fine locally, never in production")
    elif not settings().supabase_jwt_secret:
        report(BAD, "Auth", "AUTH_ENABLED=true but SUPABASE_JWT_SECRET is empty")
    else:
        report(OK, "Auth", "enabled, JWT secret present")


def main() -> int:
    print(f"\nVerifying {engine().url.render_as_string(hide_password=True)}\n")
    check_url()
    if check_connection():
        check_schema()
        check_rls()
    check_auth()
    check_the_actual_attack()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
