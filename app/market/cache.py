"""Price cache, backed by the price_snapshots table.

Sits between the provider and the services layer and does three jobs:

  1. Serves a price that is younger than the TTL without hitting the network, so opening
     the dashboard five times in a row is one fetch, not five.
  2. Persists every price it does fetch -- the cache and the history the agent layer
     needs are the same table.
  3. Falls back to the last known price when a live fetch fails, flagged stale. A stale
     price shown as stale is more useful than a blank cell, and far more useful than the
     whole refresh failing because one symbol was delisted.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Sequence

from sqlalchemy.engine import Connection

from app.config import settings
from app.core.models import Quote
from app.core.sectors import Market
from app.market.base import MarketDataProvider
from app.market.yfinance_provider import YFinanceProvider
from app.store import repository

log = logging.getLogger(__name__)


class PriceService:
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self.provider = provider or YFinanceProvider()

    def get_prices(
        self,
        conn: Connection,
        market: Market,
        tickers: Sequence[str],
        *,
        force: bool = False,
    ) -> dict[str, Quote]:
        """Prices for every ticker we can supply one for.

        force=True is the dashboard's Refresh button: skip the TTL and go to the network.
        Tickers with no price at all are simply absent -- the caller reports them as
        unpriced rather than inventing a number.
        """
        if not tickers:
            return {}

        cached = repository.latest_prices(conn, market, tickers)
        ttl = timedelta(minutes=settings().price_cache_ttl_minutes)
        cutoff = datetime.now(UTC) - ttl

        if force:
            wanted = list(tickers)
        else:
            wanted = [
                t
                for t in tickers
                if t not in cached or cached[t].as_of < cutoff
            ]

        if not wanted:
            return cached

        fetched = self.provider.get_quotes(market, wanted)
        if fetched:
            repository.upsert_prices(conn, market, fetched.values())

        quotes: dict[str, Quote] = {}
        for ticker in tickers:
            if ticker in fetched:
                quotes[ticker] = fetched[ticker]
            elif ticker in cached:
                # Live fetch failed but we have a previous price. Serve it, and say so.
                quotes[ticker] = replace(cached[ticker], stale=True)
                log.warning("serving stale price for %s (%s)", ticker, market.value)

        return quotes
