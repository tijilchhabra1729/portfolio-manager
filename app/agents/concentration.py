"""Concentration: is too much of the portfolio in one stock or one sector?

The doc's core worry — "not having a large investment in one stock or sector". Reads
allocation percentages (already cost-basis, already computed) straight off the view.
"""

from __future__ import annotations

from app.agents.base import (
    CRITICAL,
    SECTOR_CRITICAL,
    SECTOR_WARN,
    STOCK_CRITICAL,
    STOCK_WARN,
    WARNING,
    InsightDraft,
)
from app.core.models import DashboardView

SOURCE = "concentration"


def run(view: DashboardView) -> list[InsightDraft]:
    drafts: list[InsightDraft] = []

    for stock in view.stocks:
        pct = stock.allocation_pct
        if pct >= STOCK_CRITICAL:
            sev = CRITICAL
        elif pct >= STOCK_WARN:
            sev = WARNING
        else:
            continue
        drafts.append(
            InsightDraft(
                severity=sev,
                title=f"{stock.ticker} is {pct}% of your portfolio",
                body=(
                    f"{stock.name} makes up {pct}% of invested amount. A single position "
                    f"above {STOCK_WARN}% concentrates risk; above {STOCK_CRITICAL}% it "
                    "dominates the book. Consider trimming toward a more balanced weight."
                ),
                source=SOURCE,
                dedupe_key=f"stock:{stock.ticker}",
                related_ticker=stock.ticker,
                related_sector=stock.sector,
            )
        )

    for sector in view.sectors:
        pct = sector.allocation_pct
        if pct >= SECTOR_CRITICAL:
            sev = CRITICAL
        elif pct >= SECTOR_WARN:
            sev = WARNING
        else:
            continue
        drafts.append(
            InsightDraft(
                severity=sev,
                title=f"{sector.sector} is {pct}% of your portfolio",
                body=(
                    f"{sector.stock_count} holding(s) in {sector.sector} total {pct}% of "
                    f"invested amount — above the {SECTOR_WARN}% single-sector guideline. "
                    "A shock to this sector would move the whole portfolio."
                ),
                source=SOURCE,
                dedupe_key=f"sector:{sector.sector}",
                related_sector=sector.sector,
            )
        )

    return drafts
