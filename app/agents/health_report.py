"""The weekly health report.

A longer-form read than an insight: a 0–100 health score, a short narrative, and a
red-flag checklist. The checklist blends **deterministic** flags this module computes
itself (concentration, small-cap %, holdings unpriced for days) with anything the model
surfaces from the data — so the important facts are always present and never depend on the
model noticing them, while the narrative adds the colour.

If no model is available the report still generates from the deterministic flags with a
rules-based narrative; the model, when present, rewrites the prose and may add flags.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from app.agents.base import (
    SECTOR_WARN,
    SMALLCAP_WARN,
    STOCK_WARN,
)
from app.core.market_cap import SMALL
from app.core.models import DashboardView
from app.llm.base import AnalystError, AnalystModel

log = logging.getLogger(__name__)
ZERO = Decimal(0)

SYSTEM = (
    "You are a portfolio health reviewer. Given a portfolio summary, its recent history, "
    "and a list of already-detected red flags, write a brief health review for the owner. "
    "Score overall health 0-100 (100 = well-diversified, balanced, no flags). Keep the "
    "narrative to 3-5 sentences: what's healthy, what's concerning, what changed. You may "
    "add red flags you notice, but do not remove the provided ones. No buy/sell advice. "
    'Return JSON: {"health_score": int, "narrative": str, '
    '"extra_flags": [{"ok": false, "text": str}]}.'
)


class _Reply(BaseModel):
    health_score: int = 50
    narrative: str = ""
    extra_flags: list[dict] = []


def generate(
    view: DashboardView, history: list[dict], model: AnalystModel | None
) -> tuple[int, str, list[dict]]:
    """Return (health_score, narrative, red_flags)."""
    flags = _deterministic_flags(view)
    base_score = _score_from_flags(view, flags)

    if model is None:
        return base_score, _fallback_narrative(view, flags), flags

    payload = _payload(view, history, flags)
    for attempt in (1, 2):
        try:
            raw = model.complete_json(SYSTEM, payload, max_tokens=1200)
            reply = _Reply.model_validate(raw)
            score = max(0, min(100, reply.health_score))
            extra = [
                {"ok": False, "text": str(f.get("text", "")).strip()[:200]}
                for f in reply.extra_flags
                if str(f.get("text", "")).strip()
            ]
            return score, reply.narrative.strip()[:1500] or _fallback_narrative(view, flags), flags + extra
        except (AnalystError, ValidationError) as exc:
            log.warning("health report attempt %d failed: %s", attempt, exc)
    # Model failed — the deterministic report still stands.
    return base_score, _fallback_narrative(view, flags), flags


def _deterministic_flags(view: DashboardView) -> list[dict]:
    """Facts the model must never miss. Each: {ok: bool, text: str}. `ok=True` entries are
    green ticks the checklist shows alongside the problems."""
    flags: list[dict] = []
    t = view.totals

    top_stock = max(view.stocks, key=lambda s: s.allocation_pct, default=None)
    if top_stock and top_stock.allocation_pct >= STOCK_WARN:
        flags.append({"ok": False, "text": f"{top_stock.ticker} is {top_stock.allocation_pct}% of invested (guideline {STOCK_WARN}%)"})
    else:
        flags.append({"ok": True, "text": "No single stock dominates the portfolio"})

    top_sector = max(view.sectors, key=lambda s: s.allocation_pct, default=None)
    if top_sector and top_sector.allocation_pct >= SECTOR_WARN:
        flags.append({"ok": False, "text": f"{top_sector.sector} is {top_sector.allocation_pct}% of invested (guideline {SECTOR_WARN}%)"})
    else:
        flags.append({"ok": True, "text": f"Diversified across {t.sector_count} sectors"})

    small = sum((s.invested for s in view.stocks if s.cap_class == SMALL), ZERO)
    if t.invested > ZERO:
        small_pct = (small / t.invested * Decimal(100)).quantize(Decimal("0.01"))
        if small_pct >= SMALLCAP_WARN:
            flags.append({"ok": False, "text": f"Small caps are {small_pct}% of invested (guideline {SMALLCAP_WARN}%)"})
        else:
            flags.append({"ok": True, "text": f"Small-cap exposure within {SMALLCAP_WARN}%"})

    if view.unpriced:
        flags.append({"ok": False, "text": f"{len(view.unpriced)} holding(s) could not be priced: {', '.join(view.unpriced)}"})

    return flags


def _score_from_flags(view: DashboardView, flags: list[dict]) -> int:
    """A transparent starting score: 100 minus a penalty per red flag, floored. The model
    can override, but this is the answer when there's no model."""
    problems = sum(1 for f in flags if not f.get("ok"))
    return max(30, 100 - problems * 15)


def _fallback_narrative(view: DashboardView, flags: list[dict]) -> str:
    problems = [f["text"] for f in flags if not f.get("ok")]
    if not problems:
        return (
            f"Your {view.market.value} portfolio looks balanced: no single stock or "
            f"sector dominates and small-cap exposure is within guideline."
        )
    return (
        f"Your {view.market.value} portfolio has {len(problems)} item(s) worth attention: "
        + "; ".join(problems)
        + "."
    )


def _payload(view: DashboardView, history: list[dict], flags: list[dict]) -> str:
    t = view.totals
    lines = [
        f"Market: {view.market.value} ({view.currency})",
        f"Invested: {t.invested}, market value: {t.market_value}, P/L%: {t.pnl_pct}",
        f"{t.stock_count} stocks across {t.sector_count} sectors.",
        "",
        "Sector allocation:",
    ]
    for s in view.sectors:
        lines.append(f"  {s.sector}: {s.allocation_pct}% (P/L {s.pnl_pct}%)")
    lines += ["", "Already-detected red flags:"]
    lines += [f"  - {f['text']}" for f in flags if not f.get("ok")] or ["  (none)"]
    if len(history) >= 2:
        first, last = history[0], history[-1]
        lines += ["", f"History: invested went {first.get('invested')} → {last.get('invested')} over {len(history)} snapshots."]
    return "\n".join(lines)
