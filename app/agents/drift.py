"""Allocation drift: has a sector's weight moved materially over the past week?

This is the agent the snapshot history was collected *for*. It compares each sector's
allocation now against the closest snapshot roughly a week back. It stays silent until
there is enough history to mean anything — at least two snapshots spanning three days —
because history only began accruing when the daily cron first ran, and a "drift" measured
over one day is noise.

Takes the snapshot rows as an argument (oldest → newest, as `get_portfolio_history`
returns them) so the agent itself does no I/O.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.agents.base import DRIFT_POINTS, INFO, WARNING, InsightDraft
from app.core.models import DashboardView

SOURCE = "drift"
LOOKBACK_DAYS = 7
MIN_SPAN_DAYS = 3


def run(view: DashboardView, history: list[dict]) -> list[InsightDraft]:
    usable = [h for h in history if h.get("sector_allocations")]
    if len(usable) < 2:
        return []

    today = view.generated_at.date()
    baseline = _baseline(usable, today)
    if baseline is None:
        return []

    baseline_day = baseline["captured_on"]
    if isinstance(baseline_day, date) and (today - baseline_day).days < MIN_SPAN_DAYS:
        return []

    past = _as_decimals(baseline["sector_allocations"])
    now = {s.sector: s.allocation_pct for s in view.sectors}

    drafts: list[InsightDraft] = []
    for sector in set(now) | set(past):
        before = past.get(sector, Decimal(0))
        after = now.get(sector, Decimal(0))
        delta = after - before
        if abs(delta) < DRIFT_POINTS:
            continue
        direction = "up" if delta > 0 else "down"
        drafts.append(
            InsightDraft(
                # Drift is informational unless it's a large move; it describes change,
                # not a broken rule.
                severity=WARNING if abs(delta) >= DRIFT_POINTS * 2 else INFO,
                title=f"{sector} allocation moved {direction} {abs(delta):+.2f} points",
                body=(
                    f"{sector} was {before}% of invested amount around "
                    f"{baseline_day}; it is {after}% now — a shift of {delta:+.2f} points. "
                    "Drift like this comes from buying/selling within the sector, not from "
                    "price (allocation is cost-basis)."
                ),
                source=SOURCE,
                dedupe_key=f"drift:{sector}",
                related_sector=sector,
            )
        )
    return drafts


def _baseline(usable: list[dict], today: date) -> dict | None:
    """The snapshot closest to LOOKBACK_DAYS ago; if none is that old yet, the oldest."""
    target = today - timedelta(days=LOOKBACK_DAYS)
    older = [h for h in usable if isinstance(h.get("captured_on"), date) and h["captured_on"] <= today]
    if not older:
        return None
    return min(older, key=lambda h: abs((h["captured_on"] - target).days))


def _as_decimals(allocations: dict) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for sector, value in (allocations or {}).items():
        try:
            out[sector] = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return out
