"""The market agent — one per market, the middle of the hierarchy.

It reads its sector agents' findings (its peers' output on the shared blackboard) and
reasons *across* them: a single market-level direction, a short summary, and cross-sector
notes that connect sectors moving on the same driver ("Financial services and NBFC both
reacting to the rate decision"). It does not re-open the sectors — it synthesises what they
found.

Like every node it never raises: no model, or a failed call, falls back to a deterministic
tally of the sector directions weighted by significance.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.agents.briefing.state import (
    HIGH,
    LOW,
    MEDIUM,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    MarketBrief,
    MarketReply,
    SectorFinding,
)
from app.llm.base import AnalystError, AnalystModel

log = logging.getLogger(__name__)

_WEIGHT = {HIGH: 3, MEDIUM: 2, LOW: 1}

_SYSTEM = (
    "You are a market strategist reviewing one market of a user's portfolio. You are given "
    "the findings your sector analysts produced. Synthesise them for the owner: judge the "
    "overall direction for this market ('positive', 'negative', or 'neutral'), write a "
    "2-4 sentence summary of what happened and what it indicates, and add cross-sector "
    "notes ONLY where two or more sectors are moving on the same driver (a shared rate, "
    "currency, or commodity move). Do not invent findings beyond what the analysts "
    "reported. No buy/sell advice. "
    'Return JSON: {"overall_direction": "positive|negative|neutral", "summary": str, '
    '"cross_sector_notes": str}.'
)


def synthesize(
    market: str, currency: str, findings: list[dict], model: AnalystModel | None
) -> MarketBrief:
    sectors = [SectorFinding.model_validate(f) for f in findings]

    if model is None or not sectors:
        return _deterministic(market, sectors)

    reply = _call_with_retry(model, _payload(market, currency, sectors))
    if reply is None:
        return _deterministic(market, sectors)

    return MarketBrief(
        market=market,
        overall_direction=reply.overall_direction,
        summary=reply.summary.strip()[:900] or _fallback_summary(market, sectors),
        cross_sector_notes=reply.cross_sector_notes.strip()[:600],
        sectors=sectors,
    )


def _call_with_retry(model: AnalystModel, payload: str) -> MarketReply | None:
    for attempt in (1, 2):
        try:
            raw = model.complete_json(_SYSTEM, payload, max_tokens=550)
            return MarketReply.model_validate(raw)
        except (AnalystError, ValidationError) as exc:
            log.warning("market agent attempt %d failed (%s): %s", attempt, model.name, exc)
    return None


def _payload(market: str, currency: str, sectors: list[SectorFinding]) -> str:
    lines = [f"Market: {market} ({currency})", "", "Sector analyst findings:"]
    for s in sectors:
        lines.append(f"  [{s.sector}] direction={s.direction}, significance={s.significance}")
        lines.append(f"    happened: {s.what_happened}")
        if s.what_it_indicates:
            lines.append(f"    indicates: {s.what_it_indicates}")
    return "\n".join(lines)


def _deterministic(market: str, sectors: list[SectorFinding]) -> MarketBrief:
    return MarketBrief(
        market=market,
        overall_direction=_tally(sectors),
        summary=_fallback_summary(market, sectors),
        cross_sector_notes="",
        sectors=sectors,
    )


def _tally(sectors: list[SectorFinding]) -> str:
    score = 0
    for s in sectors:
        w = _WEIGHT.get(s.significance, 1)
        if s.direction == POSITIVE:
            score += w
        elif s.direction == NEGATIVE:
            score -= w
    return POSITIVE if score > 0 else NEGATIVE if score < 0 else NEUTRAL


def _fallback_summary(market: str, sectors: list[SectorFinding]) -> str:
    if not sectors:
        return f"No held sectors to review in {market}."
    pos = [s.sector for s in sectors if s.direction == POSITIVE]
    neg = [s.sector for s in sectors if s.direction == NEGATIVE]
    parts = [f"Reviewed {len(sectors)} sector(s) in {market}."]
    if pos:
        parts.append(f"Positive: {', '.join(pos)}.")
    if neg:
        parts.append(f"Negative: {', '.join(neg)}.")
    if not pos and not neg:
        parts.append("No clear directional signal across sectors.")
    return " ".join(parts)
