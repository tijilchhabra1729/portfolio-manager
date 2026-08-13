"""The orchestrator — the top of the hierarchy and the author of the deliverable.

It reads both market agents' briefs and reasons *across* markets (a USD move that helps US
IT margins but hurts an Indian importer; the same commodity showing up in two books). Out
comes the `Briefing` the user reads: a headline, an overall direction, a short summary, and
two plain lists — what's going well (positives) and what to watch (negatives).

No model, or a failed call, still produces a briefing: the direction is tallied from the
market briefs and the positives/negatives are assembled from the sector findings. The tab
is never empty.
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
    Briefing,
    MarketBrief,
    OrchestratorReply,
)
from app.llm.base import AnalystError, AnalystModel

log = logging.getLogger(__name__)

_WEIGHT = {HIGH: 3, MEDIUM: 2, LOW: 1}

_SYSTEM = (
    "You are the lead portfolio strategist writing a briefing for the owner. You are given "
    "each market's brief (built by market agents from their sector analysts). Write the "
    "owner-facing briefing: a short headline, the overall direction across the whole "
    "portfolio ('positive', 'negative', or 'neutral'), a 2-4 sentence summary, and two "
    "lists — 'positives' (what went well) and 'negatives' (what to watch), each a few "
    "concrete one-line items that name the sector or ticker. Reason across markets where "
    "there is a shared driver. Do not invent anything beyond the briefs. No buy/sell "
    "advice. "
    'Return JSON: {"headline": str, "overall_direction": "positive|negative|neutral", '
    '"summary": str, "positives": [str], "negatives": [str]}.'
)


def synthesize(
    market_briefs: list[dict],
    market_ctx: dict | None,
    model: AnalystModel | None,
) -> Briefing:
    briefs = [MarketBrief.model_validate(b) for b in market_briefs]
    generated_by = model.name if model is not None else "rules"

    if model is None or not briefs:
        return _deterministic(briefs, generated_by)

    reply = _call_with_retry(model, _payload(briefs, market_ctx or {}))
    if reply is None:
        return _deterministic(briefs, generated_by)

    positives = [p.strip()[:200] for p in reply.positives if p.strip()][:6]
    negatives = [n.strip()[:200] for n in reply.negatives if n.strip()][:6]
    if not positives and not negatives:
        # A model that returned empty lists still gets the deterministic backfill, so the
        # two columns are never both blank when there are findings to show.
        d = _split_findings(briefs)
        positives, negatives = d[0], d[1]
    return Briefing(
        headline=reply.headline.strip()[:160] or _headline(_tally(briefs)),
        overall_direction=reply.overall_direction,
        summary=reply.summary.strip()[:1000] or _fallback_summary(briefs),
        positives=positives,
        negatives=negatives,
        markets=briefs,
        generated_by=generated_by,
    )


def _call_with_retry(model: AnalystModel, payload: str) -> OrchestratorReply | None:
    for attempt in (1, 2):
        try:
            raw = model.complete_json(_SYSTEM, payload, max_tokens=900)
            return OrchestratorReply.model_validate(raw)
        except (AnalystError, ValidationError) as exc:
            log.warning("orchestrator attempt %d failed (%s): %s", attempt, model.name, exc)
    return None


def _payload(briefs: list[MarketBrief], market_ctx: dict) -> str:
    lines = ["Market briefs:"]
    for b in briefs:
        ctx = market_ctx.get(b.market, {})
        pl = ctx.get("pnl_pct")
        head = f"[{b.market}] direction={b.overall_direction}"
        if pl is not None:
            head += f", portfolio P/L {pl:+.1f}%"
        lines.append(head)
        lines.append(f"  summary: {b.summary}")
        if b.cross_sector_notes:
            lines.append(f"  cross-sector: {b.cross_sector_notes}")
        for s in b.sectors:
            lines.append(f"  - {s.sector}: {s.direction} — {s.what_happened}")
    return "\n".join(lines)


def _deterministic(briefs: list[MarketBrief], generated_by: str) -> Briefing:
    if not briefs:
        return Briefing(
            headline="Nothing to brief yet",
            overall_direction=NEUTRAL,
            summary="No holdings to review. Upload a portfolio to get a briefing.",
            generated_by=generated_by,
        )
    direction = _tally(briefs)
    positives, negatives = _split_findings(briefs)
    return Briefing(
        headline=_headline(direction),
        overall_direction=direction,
        summary=_fallback_summary(briefs),
        positives=positives,
        negatives=negatives,
        markets=briefs,
        generated_by=generated_by,
    )


def _tally(briefs: list[MarketBrief]) -> str:
    score = 0
    for b in briefs:
        for s in b.sectors:
            w = _WEIGHT.get(s.significance, 1)
            if s.direction == POSITIVE:
                score += w
            elif s.direction == NEGATIVE:
                score -= w
    return POSITIVE if score > 0 else NEGATIVE if score < 0 else NEUTRAL


def _split_findings(briefs: list[MarketBrief]) -> tuple[list[str], list[str]]:
    """Positives and negatives assembled straight from the sector findings, most
    significant first, so the two columns are populated even with no model."""
    order = {HIGH: 0, MEDIUM: 1, LOW: 2}
    findings = [(b.market, s) for b in briefs for s in b.sectors]
    findings.sort(key=lambda ms: order.get(ms[1].significance, 3))
    positives = [f"{s.sector} ({m}): {s.what_happened}" for m, s in findings if s.direction == POSITIVE][:6]
    negatives = [f"{s.sector} ({m}): {s.what_happened}" for m, s in findings if s.direction == NEGATIVE][:6]
    return positives, negatives


def _headline(direction: str) -> str:
    return {
        POSITIVE: "Your portfolio moved in your favour this week",
        NEGATIVE: "A few things in your portfolio need attention",
        NEUTRAL: "A quiet week across your portfolio",
    }.get(direction, "Portfolio briefing")


def _fallback_summary(briefs: list[MarketBrief]) -> str:
    return " ".join(f"{b.market}: {b.summary}" for b in briefs)
