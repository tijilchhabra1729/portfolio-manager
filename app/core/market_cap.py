"""Company-size classification.

The doc's "small caps should ideally be less than 5%" rule needs a definition of "small
cap", and there is no single official one — SEBI/AMFI rank by position rather than a fixed
rupee cutoff, and US practice varies by index provider. These thresholds are therefore
deliberate approximations, stated in the UI and in any insight so the reader knows the
basis rather than trusting a hidden number.

Pure functions over Decimal, no I/O — the classification travels through `core` with the
rest of the portfolio maths and is testable offline.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.sectors import Market

LARGE = "large"
MID = "mid"
SMALL = "small"

# Upper bound of each band, in the market's own currency. A cap at or below the "small"
# bound is small; at or below "mid" is mid; anything larger is large.
#   India: ₹5,000 cr small / ₹20,000 cr mid  (1 crore = 10,000,000)
#   US:    $2B small / $10B mid
_CRORE = Decimal(10_000_000)
_BILLION = Decimal(1_000_000_000)

BANDS: dict[Market, tuple[Decimal, Decimal]] = {
    Market.INDIA: (5_000 * _CRORE, 20_000 * _CRORE),
    Market.US: (2 * _BILLION, 10 * _BILLION),
}


def classify(market: Market, market_cap: Decimal | None) -> str | None:
    """small / mid / large, or None when the cap is unknown.

    Unknown is a distinct answer, never silently "large": a missing cap must not let a
    genuinely small position hide from the < 5% rule.
    """
    if market_cap is None or market_cap <= 0:
        return None
    small_max, mid_max = BANDS[market]
    if market_cap <= small_max:
        return SMALL
    if market_cap <= mid_max:
        return MID
    return LARGE


def band_label(market: Market) -> str:
    """Human description of this market's small-cap cutoff, for insight bodies."""
    if market is Market.INDIA:
        return "under ₹5,000 cr"
    return "under $2B"
