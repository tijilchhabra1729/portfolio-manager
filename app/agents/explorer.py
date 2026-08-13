"""Explore: a fundamental read of any one ticker.

Given the ratios `YFinanceFundamentals` pulled and a few headlines, the model returns a
structured read — a one-line verdict on valuation, profitability, leverage and momentum,
plus a short overall take. The ratios themselves are always returned to the caller even
when the LLM step is unavailable, so the Explore tab is useful with no key at all; the
model just adds interpretation.

Same discipline as the analyst: schema-validated, retried once, and any failure returns a
"couldn't analyse" marker rather than raising.
"""

from __future__ import annotations

import logging
from typing import Sequence

from pydantic import BaseModel, ValidationError

from app.llm.base import AnalystError, AnalystModel
from app.market.fundamentals import Fundamentals
from app.market.news import Headline

log = logging.getLogger(__name__)

SYSTEM = (
    "You are an equity analyst. Given one stock's fundamental ratios and recent "
    "headlines, write a concise, balanced read for a retail investor deciding whether to "
    "research it further. Be specific to the numbers provided; do not invent data. No "
    "buy/sell/hold call. "
    'Return JSON: {"valuation": str, "profitability": str, "leverage": str, '
    '"momentum": str, "overall": str}. Each of valuation/profitability/leverage/momentum '
    "is one short sentence; overall is 2-3 sentences. If a ratio is missing, say so "
    "rather than guessing."
)


class StockRead(BaseModel):
    valuation: str = ""
    profitability: str = ""
    leverage: str = ""
    momentum: str = ""
    overall: str = ""


def run(
    fundamentals: Fundamentals, headlines: Sequence[Headline], model: AnalystModel
) -> tuple[StockRead | None, str | None]:
    """Return (read, error). Exactly one is non-None."""
    payload = _payload(fundamentals, headlines)
    for attempt in (1, 2):
        try:
            raw = model.complete_json(SYSTEM, payload, max_tokens=900)
            return StockRead.model_validate(raw), None
        except (AnalystError, ValidationError) as exc:
            log.warning("explore attempt %d failed (%s): %s", attempt, model.name, exc)
            last = str(exc)
    return None, last


def _payload(f: Fundamentals, headlines: Sequence[Headline]) -> str:
    def show(label: str, value, unit: str = "") -> str:
        return f"  {label}: {value}{unit}" if value is not None else f"  {label}: n/a"

    lines = [
        f"{f.name or f.ticker} ({f.ticker}) — {f.sector or 'sector n/a'}, "
        f"{f.currency or ''}",
        show("Price", f.price),
        show("Market cap", f.market_cap),
        show("P/E (trailing)", f.pe),
        show("P/B", f.pb),
        show("Return on equity", f.roe_pct, "%"),
        show("Profit margin", f.profit_margin_pct, "%"),
        show("Debt / equity", f.debt_to_equity),
        show("Revenue growth", f.revenue_growth_pct, "%"),
        show("Dividend yield", f.dividend_yield_pct, "%"),
        show("Beta", f.beta),
        show("52-week high", f.week52_high),
        show("52-week low", f.week52_low),
    ]
    if headlines:
        lines.append("Recent headlines:")
        for h in headlines[:5]:
            lines.append(f"  - {h.title}")
    return "\n".join(lines)
