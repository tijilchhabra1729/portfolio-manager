"""Shared vocabulary for the agents.

An agent takes a built DashboardView (and, for drift, some history) and returns
InsightDrafts. The runner publishes them. Nothing here touches the database or the network
— the rule agents are pure functions over the view, testable offline, and an agent can be
run against a *hypothetical* portfolio exactly as phase 1 promised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# --- severities (match the UI's banner classes) ---
INFO = "info"
WARNING = "warning"
CRITICAL = "critical"

# --- thresholds -------------------------------------------------------------------
# All judgment calls, kept here so they are visible and tunable in one place. The doc
# frames a ~1Cr / ~20-stock portfolio, so an evenly split book is ~5% per stock; these
# fire well above that.
STOCK_WARN = Decimal(10)      # a single stock over 10% of invested
STOCK_CRITICAL = Decimal(20)  #                       over 20%
SECTOR_WARN = Decimal(25)     # a sector over 25% of invested
SECTOR_CRITICAL = Decimal(40) #            over 40%
SMALLCAP_WARN = Decimal(5)    # the doc's own rule: small caps should be under 5%
SMALLCAP_CRITICAL = Decimal(10)
DRIFT_POINTS = Decimal(5)     # a sector allocation that moved >= 5 percentage points


@dataclass(frozen=True)
class InsightDraft:
    """What an agent produces. `dedupe_key` is the stable identity of the condition or
    event, so a nightly rerun updates the same row rather than stacking a copy."""

    severity: str
    title: str
    body: str
    source: str
    dedupe_key: str
    related_ticker: str | None = None
    related_sector: str | None = None
