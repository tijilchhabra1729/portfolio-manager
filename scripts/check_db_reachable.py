"""Can this machine even open a TCP socket to the database?

    .venv/bin/python -m scripts.check_db_reachable

A timeout from alembic or psycopg looks like a database problem but usually is not: many
campus, corporate and cafe networks block outbound 5432. This tests the socket only --
no credentials, no queries -- and tries the alternate Supabase pooler port too, so you
learn whether it is the network or the connection string. Prints no secrets.
"""

from __future__ import annotations

import socket
import sys
from urllib.parse import urlsplit

from app.config import settings

TIMEOUT = 8.0
SESSION_POOLER, TRANSACTION_POOLER = 5432, 6543


def probe(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True, "open"
    except socket.timeout:
        return False, f"timed out after {TIMEOUT:.0f}s (port almost certainly blocked)"
    except socket.gaierror as exc:
        return False, f"DNS failure: {exc}"
    except OSError as exc:
        return False, f"{exc}"


def main() -> int:
    url = urlsplit(settings().database_url.replace("postgresql+psycopg", "https"))
    host, port = url.hostname or "", url.port or SESSION_POOLER
    print(f"\nhost: {host}\nport: {port}\n")

    if host in ("localhost", "127.0.0.1"):
        ok, why = probe(host, port)
        print(f"  {'OK ' if ok else 'FAIL'}  {host}:{port} — {why}")
        if not ok:
            print("\n  Local Postgres is not up. Run: docker compose up -d")
        return 0 if ok else 1

    results = {}
    for candidate in (SESSION_POOLER, TRANSACTION_POOLER):
        ok, why = probe(host, candidate)
        results[candidate] = ok
        label = "session pooler" if candidate == SESSION_POOLER else "transaction pooler"
        print(f"  {'OK  ' if ok else 'FAIL'}  {host}:{candidate}  ({label}) — {why}")

    print()
    if results.get(port):
        print("Your configured port is reachable. If migrations still fail, the problem is\n"
              "the credentials or the database name, not the network.")
        return 0

    if any(results.values()):
        working = next(p for p, ok in results.items() if ok)
        print(f"Port {port} is blocked, but {working} is open.\n"
              f"Edit DATABASE_URL in .env to use :{working} instead. The app adapts to\n"
              f"either pooler mode automatically (see app/store/db.py).")
        return 1

    print("Both pooler ports are blocked from this network — this is a firewall, not a\n"
          "Supabase problem. Campus and corporate networks routinely block outbound 5432.\n"
          "\nThree ways forward:\n"
          "  1. Tether to your phone's hotspot and re-run. Usually just works.\n"
          "  2. Skip the local step entirely: the Dockerfile runs `alembic upgrade head`\n"
          "     on boot, so deploying to Render applies the migrations from Render's\n"
          "     network. Then verify RLS from the Supabase SQL editor in your browser\n"
          "     (see DEPLOY.md).\n"
          "  3. Run the migration SQL by hand in the Supabase SQL editor.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
