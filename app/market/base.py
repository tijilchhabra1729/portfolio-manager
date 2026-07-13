"""The market-data seam.

Everything above this layer asks for quotes by plain ticker and never learns that
yfinance wants RELIANCE.NS. Swapping in a paid feed later means writing one class, not
touching the dashboard.

The agent layer will add siblings here -- a news provider, a fundamentals provider --
following the same shape.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from app.core.models import Quote
from app.core.sectors import Market


class MarketDataProvider(Protocol):
    def get_quotes(self, market: Market, tickers: Sequence[str]) -> dict[str, Quote]:
        """Price as many of the tickers as possible, keyed by plain ticker.

        Implementations must not raise when a single ticker fails. A dead symbol is
        expected -- it is left out of the returned mapping and the dashboard reports it
        as unpriced rather than the whole refresh collapsing.
        """
        ...
