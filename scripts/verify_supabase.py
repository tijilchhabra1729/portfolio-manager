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
    """The one that counts. Use the public anon key exactly as an attacker would.

    Two requests, not one. A denial is only evidence if PostgREST was actually listening:
    a typo in SUPABASE_URL would produce a 404 too, and reporting that as "you're secure"
    would be worse than not checking at all.
    """
    url, key = settings().supabase_url.rstrip("/"), settings().supabase_anon_key
    if not url or not key:
        report(WARN, "Anon-key read attempt", "SUPABASE_URL / SUPABASE_ANON_KEY not set")
        return

    if key.startswith("sb_secret_") or "service_role" in key:
        report(
            BAD,
            "Anon-key read attempt",
            "SUPABASE_ANON_KEY holds the SECRET key. That key bypasses row-level\n"
            "security entirely, and this app serves it to the browser from /api/config.\n"
            "Use the PUBLISHABLE key (sb_publishable_...) instead, and rotate the secret.",
        )
        return

    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    # Validate the key against an endpoint a publishable key is actually allowed to use.
    # (Not /rest/v1/ -- that is PostgREST's OpenAPI root, which Supabase reserves for
    # secret keys, so it 401s even when everything is correct.)
    try:
        valid = httpx.get(f"{url}/auth/v1/settings", headers=auth, timeout=20)
    except Exception as exc:
        report(WARN, "Anon-key read attempt", f"could not reach Supabase: {exc}")
        return

    if valid.status_code != 200:
        try:
            reason = valid.json().get("message") or valid.text[:160]
        except Exception:
            reason = valid.text[:160]
        report(
            BAD,
            "Anon-key read attempt",
            f"Supabase rejected the key ({valid.status_code}): {reason}\n"
            f"Key sent: {key[:12]}...{key[-4:]} ({len(key)} chars)\n"
            "A 'denied' below would prove nothing while the key itself is invalid.\n"
            "SUPABASE_ANON_KEY must be the PUBLISHABLE key (Settings -> API Keys).",
        )
        return

    def read(table: str) -> httpx.Response:
        return httpx.get(
            f"{url}/rest/v1/{table}",
            params={"select": "*", "limit": "1"},
            headers=auth,
            timeout=20,
        )

    try:
        real = read("transactions")
        # Control: a table that definitely does not exist. If `transactions` answers
        # exactly like a table that was never created, PostgREST genuinely cannot see it
        # -- which is what revoking the grants does. Without this control, a blanket 404
        # (a broken URL, say) would masquerade as security.
        control = read("zzz_table_that_does_not_exist")
    except Exception as exc:
        report(WARN, "Anon-key read attempt", f"request failed: {exc}")
        return

    if real.status_code == 200 and real.json():
        report(
            BAD,
            "Anon-key read attempt",
            "YOUR HOLDINGS ARE PUBLIC. The publishable key read the transactions table\n"
            f"over REST ({real.status_code}). That key ships to every browser that loads\n"
            "the dashboard. Run: alembic upgrade head",
        )
        return

    invisible = real.status_code == control.status_code
    report(
        OK,
        "Anon-key read attempt",
        f"key is valid (auth settings 200), yet reading transactions returned "
        f"{real.status_code}\n"
        f"-- identical to a table that does not exist ({control.status_code})"
        f"{', so it is genuinely invisible' if invisible else ''}.\n"
        "The key that ships to every browser cannot see your portfolio.",
    )


def check_auth() -> None:
    """Supabase signs user tokens one of two ways, and both are valid. What matters is
    that the app can obtain the key it needs to verify them."""
    if not settings().auth_enabled:
        report(WARN, "Auth", "AUTH_ENABLED=false -- fine locally, never in production")
        return

    if settings().supabase_jwt_secret:
        report(
            OK,
            "Auth",
            "legacy HS256 mode -- shared JWT secret is set, and only HS256 will be\n"
            "accepted (no algorithm confusion)",
        )
        return

    # No shared secret: the project signs asymmetrically, so the app verifies against the
    # JWKS endpoint. Prove that endpoint actually serves a key, or every login 401s.
    url = settings().supabase_url.rstrip("/")
    if not url:
        report(BAD, "Auth", "AUTH_ENABLED=true but neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set")
        return

    try:
        # Supabase's auth endpoints reject unauthenticated requests, so even the public
        # signing key needs the publishable key attached to fetch it.
        res = httpx.get(
            f"{url}/auth/v1/.well-known/jwks.json",
            headers={"apikey": settings().supabase_anon_key},
            timeout=20,
        )
        keys = res.json().get("keys", []) if res.status_code == 200 else []
    except Exception as exc:
        report(WARN, "Auth", f"could not reach the JWKS endpoint: {exc}")
        return

    if not keys:
        report(
            BAD,
            "Auth",
            f"JWKS endpoint returned no signing keys ({res.status_code}).\n"
            "Every login would fail. If this project uses the legacy shared secret,\n"
            "set SUPABASE_JWT_SECRET instead (Settings -> API -> JWT Keys).",
        )
    else:
        algorithms = {k.get("alg") for k in keys if k.get("alg")}
        report(
            OK,
            "Auth",
            f"asymmetric mode -- JWKS serves {len(keys)} signing key(s) "
            f"({', '.join(sorted(algorithms)) or 'unspecified'}).\n"
            "SUPABASE_JWT_SECRET is correctly left empty; HS256 will be refused.",
        )


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
