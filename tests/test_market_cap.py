from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.market_cap import LARGE, MID, SMALL, classify
from app.core.sectors import Market

D = Decimal
CR = D(10_000_000)
BN = D(1_000_000_000)


@pytest.mark.parametrize(
    "market,cap,expected",
    [
        # India: small ≤ ₹5,000cr, mid ≤ ₹20,000cr
        (Market.INDIA, 4_000 * CR, SMALL),
        (Market.INDIA, 5_000 * CR, SMALL),   # boundary is inclusive
        (Market.INDIA, 5_001 * CR, MID),
        (Market.INDIA, 20_000 * CR, MID),
        (Market.INDIA, 20_001 * CR, LARGE),
        (Market.INDIA, 1_754_620 * CR, LARGE),  # Reliance-scale
        # US: small ≤ $2B, mid ≤ $10B
        (Market.US, 1 * BN, SMALL),
        (Market.US, 2 * BN, SMALL),
        (Market.US, 3 * BN, MID),
        (Market.US, 10 * BN, MID),
        (Market.US, 11 * BN, LARGE),
    ],
)
def test_classify_boundaries(market, cap, expected):
    assert classify(market, cap) == expected


def test_unknown_cap_is_none_not_large():
    # A missing cap must never masquerade as large -- it would let a small position hide
    # from the < 5% rule.
    assert classify(Market.INDIA, None) is None
    assert classify(Market.US, D(0)) is None
    assert classify(Market.INDIA, D(-5)) is None
