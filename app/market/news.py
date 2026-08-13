"""Headlines for a ticker, from yfinance.

Two things make this fiddly, and both are handled defensively:

1. yfinance's `.news` shape has drifted. Older versions returned flat dicts with
   `title` / `providerPublishTime` (epoch); current versions nest everything under a
   `content` key with `pubDate` (ISO). We read whichever is present and skip anything we
   can't parse rather than raising -- a missing headline is not worth failing an analysis.
2. Headline text is untrusted third-party input. It is length-capped here, and the UI
   renders every field via textContent, so a crafted title cannot inject markup. It still
   flows into an LLM prompt, so the analyst's instructions are written to treat headlines
   as data to reason about, not instructions to follow.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, Sequence

import yfinance as yf

from app.core.sectors import Market, yf_symbol

log = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_SUMMARY = 400


@dataclass(frozen=True)
class Headline:
    ticker: str
    title: str
    summary: str
    publisher: str
    published: datetime | None


class NewsProvider(Protocol):
    def get_news(
        self, market: Market, tickers: Sequence[str], *, per_ticker: int, within_days: int
    ) -> list[Headline]:
        ...


class YFinanceNewsProvider:
    # Bound the fan-out: a datacenter IP fetching 25 tickers serially is slow and
    # 25 threads is rude. A small pool is the balance.
    def __init__(self, max_workers: int = 6) -> None:
        self.max_workers = max_workers

    def get_news(
        self,
        market: Market,
        tickers: Sequence[str],
        *,
        per_ticker: int = 3,
        within_days: int = 7,
        max_tickers: int = 25,
    ) -> list[Headline]:
        tickers = list(dict.fromkeys(tickers))[:max_tickers]
        if not tickers:
            return []
        cutoff = datetime.now(UTC) - timedelta(days=within_days)

        headlines: list[Headline] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for batch in pool.map(
                lambda t: self._for_ticker(market, t, per_ticker, cutoff), tickers
            ):
                headlines.extend(batch)
        return headlines

    def _for_ticker(
        self, market: Market, ticker: str, per_ticker: int, cutoff: datetime
    ) -> list[Headline]:
        try:
            raw = yf.Ticker(yf_symbol(ticker, market)).news or []
        except Exception:
            log.debug("news fetch failed for %s", ticker, exc_info=True)
            return []

        out: list[Headline] = []
        for item in raw:
            parsed = _parse_item(ticker, item)
            if parsed is None:
                continue
            if parsed.published and parsed.published < cutoff:
                continue
            out.append(parsed)
            if len(out) >= per_ticker:
                break
        return out


def _parse_item(ticker: str, item: dict) -> Headline | None:
    # Current shape nests under "content"; older shape is flat. Try content first, fall
    # back to the item itself.
    content = item.get("content") if isinstance(item.get("content"), dict) else item
    title = (content.get("title") or "").strip()
    if not title:
        return None

    summary = (content.get("summary") or content.get("description") or "").strip()
    publisher = _publisher(content)
    published = _published(content, item)

    return Headline(
        ticker=ticker,
        title=title[:MAX_TITLE],
        summary=summary[:MAX_SUMMARY],
        publisher=publisher[:60],
        published=published,
    )


def _publisher(content: dict) -> str:
    provider = content.get("provider")
    if isinstance(provider, dict):
        return str(provider.get("displayName") or provider.get("name") or "")
    return str(content.get("publisher") or provider or "")


def _published(content: dict, item: dict) -> datetime | None:
    # New: ISO string in content.pubDate. Old: epoch seconds in providerPublishTime.
    iso = content.get("pubDate") or content.get("displayTime")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            pass
    epoch = item.get("providerPublishTime") or content.get("providerPublishTime")
    if isinstance(epoch, (int, float)) and epoch > 0:
        return datetime.fromtimestamp(epoch, tz=UTC)
    return None
