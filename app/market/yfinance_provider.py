"""yfinance-backed quotes.

Two things this does that a naive fetch would not:

**It retries a failed symbol on the market's other exchange.** RELIANCE.NS resolves;
plenty of Indian stocks are listed on only one of NSE or BSE, so a miss on .NS is worth a
second look at .BO before giving up.

**It refuses a quote in the wrong currency.** Ticker symbols are not globally unique --
IEX is Indian Energy Exchange on the NSE and an energy company on the NASDAQ. A fallback
that wandered across markets would price a US holding with a rupee quote and label the
result in dollars, and the P/L would look entirely plausible. Every quote is checked
against the market's own currency and dropped if it does not match. A missing price is
recoverable; a confidently wrong one is not.

Requests are batched per attempt rather than fired per ticker: from a datacenter IP
(which is where this runs in production) Yahoo throttles per-symbol fetches hard.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Sequence

import yfinance as yf

from app.core.models import Quote
from app.core.sectors import Market, spec, yf_candidates

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
    Quantizing to the column's own scale is what discards it.
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

        expected_currency = spec(market).currency
        outstanding = list(dict.fromkeys(tickers))
        quotes: dict[str, Quote] = {}

        # One batched pass per candidate suffix. Only the tickers that failed the previous
        # pass are retried, so the common case is a single request.
        attempts = max(len(yf_candidates(t, market)) for t in outstanding)
        for attempt in range(attempts):
            symbols = {
                candidates[attempt]: ticker
                for ticker in outstanding
                if attempt < len(candidates := yf_candidates(ticker, market))
            }
            if not symbols:
                break

            found = self._fetch(symbols, expected_currency)
            quotes.update(found)
            outstanding = [t for t in outstanding if t not in quotes]
            if not outstanding:
                break
            if attempt + 1 < attempts:
                log.info(
                    "retrying %d %s ticker(s) on the next exchange: %s",
                    len(outstanding), market.value, ", ".join(outstanding),
                )

        for ticker in outstanding:
            log.warning(
                "no price for %s (%s) on any of %s",
                ticker, market.value, yf_candidates(ticker, market),
            )
        return quotes

    def _fetch(
        self, symbols: dict[str, str], expected_currency: str
    ) -> dict[str, Quote]:
        try:
            batch = yf.Tickers(" ".join(symbols))
        except Exception:
            log.exception("yfinance batch construction failed")
            return {}

        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}

        for symbol, ticker in symbols.items():
            try:
                # Subscript, not .get(): FastInfo.get() raises KeyError on an unrelated
                # internal key, so it cannot be used to probe for a field.
                info = batch.tickers[symbol].fast_info
                price = _to_decimal(info["lastPrice"], PRICE_DP)
                if price is None:
                    continue

                currency = (info["currency"] or "").upper()
                if currency and currency != expected_currency:
                    # The safety net. A symbol that resolves in the wrong currency is a
                    # different company with the same ticker, not our holding.
                    log.warning(
                        "ignoring %s: quoted in %s, expected %s",
                        symbol, currency, expected_currency,
                    )
                    continue

                quotes[ticker] = Quote(
                    ticker=ticker,
                    price=price,
                    as_of=now,
                    market_cap=_to_decimal(info["marketCap"], CAP_DP),
                )
            except Exception:
                # One bad symbol must never take the batch down with it.
                log.debug("quote failed for %s", symbol, exc_info=True)

        return quotes
