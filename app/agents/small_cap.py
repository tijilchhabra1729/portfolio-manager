"""Small-cap exposure: the doc's explicit "small caps should ideally be < 5%" rule.

Sums the invested amount in holdings classified small (by `cap_class`, set in core from
market cap) and compares its share of the *whole* portfolio against the guideline.
Holdings whose market cap is unknown can't be classified, so they're excluded and the
insight says how many were skipped — an unknown cap must never masquerade as "not small".
"""

from __future__ import annotations

from decimal import Decimal

from app.agents.base import (
    CRITICAL,
    SMALLCAP_CRITICAL,
    SMALLCAP_WARN,
    WARNING,
    InsightDraft,
)
from app.core.market_cap import SMALL, band_label
from app.core.models import DashboardView

SOURCE = "small-cap"
ZERO = Decimal(0)


def run(view: DashboardView) -> list[InsightDraft]:
    total = view.totals.invested
    if total <= ZERO:
        return []

    small = [s for s in view.stocks if s.cap_class == SMALL]
    unknown = sum(1 for s in view.stocks if s.cap_class is None)
    if not small:
        return []

    small_invested = sum((s.invested for s in small), ZERO)
    pct = (small_invested / total * Decimal(100)).quantize(Decimal("0.01"))

    if pct >= SMALLCAP_CRITICAL:
        sev = CRITICAL
    elif pct >= SMALLCAP_WARN:
        sev = WARNING
    else:
        return []

    tickers = ", ".join(s.ticker for s in small)
    skipped = (
        f" ({unknown} holding(s) had no market-cap data and were left out.)"
        if unknown
        else ""
    )
    body = (
        f"Small caps ({band_label(view.market)}) are {pct}% of your invested amount — "
        f"the guideline is under {SMALLCAP_WARN}%. Small caps swing harder than the "
        f"market. Holdings: {tickers}.{skipped}"
    )
    return [
        InsightDraft(
            severity=sev,
            title=f"Small-cap exposure is {pct}%",
            body=body,
            source=SOURCE,
            dedupe_key="smallcap:market",
        )
    ]
