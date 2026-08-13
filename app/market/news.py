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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Mapping, Protocol, Sequence

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
        self,
        market: Market,
        tickers: Sequence[str],
        *,
        per_ticker: int,
        within_days: int,
        names: Mapping[str, str] | None = None,
    ) -> list[Headline]:
        """`names` maps ticker -> company name; providers that search by phrase (Google
        News) use it for precision, and providers that key on the symbol (yfinance) ignore
        it. Optional so a caller with only tickers still works."""
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
        names: Mapping[str, str] | None = None,  # unused: yfinance keys on the symbol
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


class GoogleNewsRSSProvider:
    """Headlines from Google News' public RSS search. No key, no SDK -- one HTTP GET per
    holding against `news.google.com/rss/search`.

    Two reasons this is the preferred source for the briefing over yfinance's `.news`:
    it searches by *company name* (far more precise for NSE names than a bare `.NS`
    symbol), and its result titles are unusually sentiment-legible ("X shares tumble on
    downgrade", "Y jumps to record high") -- exactly what a forward-looking read needs.

    Titles are untrusted third-party text: length-capped here, rendered via textContent in
    the UI, and the analyst prompt treats them as data to reason about, never instructions.
    """

    # Google wants a language/region triple; pick the one matching the exchange so an
    # Indian name returns Indian coverage rather than a US wire's passing mention.
    _LOCALE = {
        Market.INDIA: ("en-IN", "IN", "IN:en"),
        Market.US: ("en-US", "US", "US:en"),
    }

    def __init__(self, max_workers: int = 6, timeout: float = 10.0) -> None:
        self.max_workers = max_workers
        self.timeout = timeout

    def get_news(
        self,
        market: Market,
        tickers: Sequence[str],
        *,
        per_ticker: int = 3,
        within_days: int = 7,
        max_tickers: int = 25,
        names: Mapping[str, str] | None = None,
    ) -> list[Headline]:
        tickers = list(dict.fromkeys(tickers))[:max_tickers]
        if not tickers:
            return []
        names = names or {}
        cutoff = datetime.now(UTC) - timedelta(days=within_days)

        headlines: list[Headline] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for batch in pool.map(
                lambda t: self._for_ticker(market, t, names.get(t), per_ticker, cutoff),
                tickers,
            ):
                headlines.extend(batch)
        return headlines

    def _query(self, market: Market, ticker: str, name: str | None) -> str:
        # The company name is far more precise than an exchange symbol; the suffix keeps
        # results on the equity rather than the company's products.
        subject = name or ticker
        suffix = "stock" if market == Market.US else "share price"
        return f"{subject} {suffix}"

    def _for_ticker(
        self, market: Market, ticker: str, name: str | None, per_ticker: int, cutoff: datetime
    ) -> list[Headline]:
        hl, gl, ceid = self._LOCALE.get(market, ("en-US", "US", "US:en"))
        query = urllib.parse.quote(self._query(market, ticker, name))
        url = f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                root = ET.fromstring(resp.read())
        except Exception:
            log.debug("google news fetch failed for %s", ticker, exc_info=True)
            return []

        out: list[Headline] = []
        for item in root.iterfind(".//item"):
            parsed = _parse_rss_item(ticker, item)
            if parsed is None:
                continue
            if parsed.published and parsed.published < cutoff:
                continue
            out.append(parsed)
            if len(out) >= per_ticker:
                break
        return out


def _parse_rss_item(ticker: str, item: ET.Element) -> Headline | None:
    title = (item.findtext("title") or "").strip()
    if not title:
        return None

    # Google formats titles as "Headline - Publisher"; lift the publisher off the tail
    # from the <source> element so the title reads clean.
    publisher = ""
    source = item.find("source")
    if source is not None and (source.text or "").strip():
        publisher = source.text.strip()
        if title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)].strip()

    published: datetime | None = None
    raw = item.findtext("pubDate")
    if raw:
        try:
            published = parsedate_to_datetime(raw)
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            published = None

    return Headline(
        ticker=ticker,
        title=title[:MAX_TITLE],
        summary="",  # the RSS description is an HTML anchor, not prose — the title carries it
        publisher=publisher[:60],
        published=published,
    )


class CombinedNewsProvider:
    """Tries providers in order and fills per-holding gaps from the next one. A holding that
    the first source knows nothing about gets a second chance rather than an empty read —
    directly addressing "it can't find news" — while a holding the first source covered is
    never re-fetched, so cost and latency stay bounded."""

    def __init__(self, providers: Sequence[NewsProvider]) -> None:
        self.providers = list(providers)

    def get_news(
        self,
        market: Market,
        tickers: Sequence[str],
        *,
        per_ticker: int = 3,
        within_days: int = 7,
        names: Mapping[str, str] | None = None,
    ) -> list[Headline]:
        remaining = list(dict.fromkeys(tickers))
        collected: list[Headline] = []
        for provider in self.providers:
            if not remaining:
                break
            try:
                got = provider.get_news(
                    market, remaining, per_ticker=per_ticker, within_days=within_days, names=names
                )
            except Exception:
                log.debug("news provider %s failed", type(provider).__name__, exc_info=True)
                got = []
            collected.extend(got)
            covered = {h.ticker for h in got}
            remaining = [t for t in remaining if t not in covered]
        return collected
