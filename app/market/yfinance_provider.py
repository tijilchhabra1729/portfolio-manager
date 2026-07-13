"""yfinance-backed quotes.

Batched rather than one request per ticker: from a datacenter IP (which is where this
runs in production) Yahoo rate-limits per-symbol fetches hard, and a 25-stock portfolio
firing 25 requests is exactly the pattern that gets throttled.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Sequence

import yfinance as yf

from app.core.models import Quote
from app.core.sectors import Market, yf_symbol

log = logging.getLogger(__name__)

# Equities quote to 2dp, which is the real precision of the source. Quantizing to the
# column's full 4dp would keep a trace of the float noise (M&M arrives as
# 3161.60009765625 and would land as 3161.6001 rather than 3161.60).
PRICE_DP = Decimal("0.01")
CAP_DP = Decimal("0.01")


def _to_decimal(value: Any, places: Decimal) -> Decimal | None:
    """Clean a float from Yahoo into an exact Decimal.

    Yahoo returns prices as floats, and they arrive carrying binary noise: Reliance at
    1296.80 comes over the wire as 1296.800048828125. Going straight to Decimal would
    faithfully preserve that garbage and carry it into every downstream calculation.
    Quantizing to the column's own scale is what discards it -- this is the boundary at
    which float error would otherwise enter the system.
    """
    if value is None:
        return None
    try:
        number = Decimal(str(value)).quantize(places, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return number if number > 0 else None


class YFinanceProvider:
    def get_quotes(self, market: Market, tickers: Sequence[str]) -> dict[str, Quote]:
        if not tickers:
            return {}

        symbols = {yf_symbol(t, market): t for t in tickers}
        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}

        try:
            batch = yf.Tickers(" ".join(symbols))
        except Exception:
            log.exception("yfinance batch construction failed for %s", market)
            return {}

        for symbol, ticker in symbols.items():
            try:
                # Subscript, not .get(): FastInfo.get() raises KeyError on an unrelated
                # internal key, so it cannot be used to probe for a field.
                info = batch.tickers[symbol].fast_info
                price = _to_decimal(info["lastPrice"], PRICE_DP)
                if price is None:
                    log.warning("no price for %s", symbol)
                    continue
                quotes[ticker] = Quote(
                    ticker=ticker,
                    price=price,
                    as_of=now,
                    market_cap=_to_decimal(info["marketCap"], CAP_DP),
                )
            except Exception:
                # One bad symbol must never take the refresh down with it.
                log.warning("quote failed for %s", symbol, exc_info=True)

        return quotes
