"""The doc's "product should refresh itself daily".

Run from GitHub Actions on a cron. Re-prices every market and writes today's snapshot.

The snapshot is the point. Allocation drift, sector momentum, "your IT exposure has
climbed 6 points since April" -- none of that is answerable without a time series, and a
day that goes uncaptured cannot be recovered afterwards. The agent layer will read this
table long before it reads anything else.

It also keeps the lights on: Supabase pauses a free project after 7 days of inactivity,
and this job touching the database every day is what prevents that.
"""

from __future__ import annotations

import logging
import sys

from app.core.sectors import Market
from app.services import dashboard_service
from app.store import repository
from app.store.db import connect

log = logging.getLogger("daily_refresh")


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    failures = 0

    with connect() as conn:
        for user_id in repository.get_user_ids(conn):
            for market in Market:
                try:
                    view = dashboard_service.snapshot(conn, user_id, market)
                    log.info(
                        "%s %s: %d holdings, invested=%s, market value=%s%s",
                        user_id,
                        market.value,
                        view.totals.stock_count,
                        view.totals.invested,
                        view.totals.market_value,
                        f", unpriced={list(view.unpriced)}" if view.unpriced else "",
                    )
                except Exception:
                    # One market failing must not cost us the other's snapshot.
                    log.exception("snapshot failed for %s / %s", user_id, market.value)
                    failures += 1

    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
