"""The doc's own worked examples, plus the FIFO rules it leaves open."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.calculations import (
    InsufficientUnitsError,
    build_dashboard,
    build_positions,
)
from app.core.models import Instrument, Quote, Transaction, TxnType
from app.core.sectors import Market

D = Decimal
NOW = datetime(2026, 7, 13)


def buy(ticker: str, units: str, price: str, day: date = date(2026, 1, 1), seq: int = 0):
    return Transaction(ticker, Market.INDIA, TxnType.BUY, D(units), D(price), day, seq)


def sell(ticker: str, units: str, day: date = date(2026, 6, 1), seq: int = 99):
    return Transaction(ticker, Market.INDIA, TxnType.SELL, D(units), D(0), day, seq)


def instrument(ticker: str, sector: str = "Auto"):
    return Instrument(ticker, Market.INDIA, f"{ticker} Ltd", sector)


def quote(ticker: str, price: str):
    return Quote(ticker, D(price), NOW)


def dashboard(txns, instruments, quotes):
    return build_dashboard(
        Market.INDIA,
        build_positions(txns),
        {i.ticker: i for i in instruments},
        {q.ticker: q for q in quotes},
        now=NOW,
    )


# --- The stock table, exactly as printed in the doc ---------------------------------


def test_doc_stock_table_row_one():
    """XX: 100 units, 10,000 invested, 25,000 market value -> +15,000 (150%)."""
    view = dashboard(
        [buy("XX", "100", "100")], [instrument("XX")], [quote("XX", "250")]
    )
    row = view.stocks[0]
    assert row.units == D("100")
    assert row.invested == D("10000.00")
    assert row.market_value == D("25000.00")
    assert row.pnl == D("15000.00")
    assert row.pnl_pct == D("150.00")


def test_doc_stock_table_row_two():
    """YY: 100 units, 10,000 invested, 5,000 market value -> -5,000 (-50%)."""
    view = dashboard([buy("YY", "100", "100")], [instrument("YY")], [quote("YY", "50")])
    row = view.stocks[0]
    assert row.market_value == D("5000.00")
    assert row.pnl == D("-5000.00")
    assert row.pnl_pct == D("-50.00")


def test_doc_allocation_example():
    """'if stock x investment amount is 10000 and total portfolio investment amount is
    1,00,000 then allocation % should be 10%'."""
    view = dashboard(
        [buy("X", "100", "100"), buy("BIG", "900", "100")],  # 10,000 + 90,000
        [instrument("X"), instrument("BIG")],
        [],
    )
    rows = {r.ticker: r for r in view.stocks}
    assert view.totals.invested == D("100000.00")
    assert rows["X"].allocation_pct == D("10.00")
    assert rows["BIG"].allocation_pct == D("90.00")


def test_doc_sector_allocation_example():
    """'if stock x & y belong to auto sector having an investment amount is 10000 each
    and total portfolio investment amount is 1,00,000 then Auto sector % allocation
    should be 20%'."""
    view = dashboard(
        [
            buy("X", "100", "100"),  # 10,000 Auto
            buy("Y", "100", "100"),  # 10,000 Auto
            buy("BIG", "800", "100"),  # 80,000 IT
        ],
        [instrument("X", "Auto"), instrument("Y", "Auto"), instrument("BIG", "IT")],
        [],
    )
    sectors = {s.sector: s for s in view.sectors}
    assert view.totals.invested == D("100000.00")
    assert sectors["Auto"].allocation_pct == D("20.00")
    assert sectors["Auto"].invested == D("20000.00")
    assert sectors["Auto"].stock_count == 2
    assert sectors["IT"].allocation_pct == D("80.00")


def test_doc_sector_table_aggregates_market_value():
    """Sector market value is the sum of its stocks' market values."""
    view = dashboard(
        [buy("A", "100", "100"), buy("B", "100", "100")],
        [instrument("A", "Auto"), instrument("B", "Auto")],
        [quote("A", "250"), quote("B", "50")],
    )
    auto = view.sectors[0]
    assert auto.invested == D("20000.00")
    assert auto.market_value == D("30000.00")  # 25,000 + 5,000
    assert auto.pnl == D("10000.00")
    assert auto.pnl_pct == D("50.00")


# --- The rule that is easiest to get wrong ------------------------------------------


def test_allocation_uses_cost_basis_not_market_value():
    """The doc states twice that allocation is NOT based on market value. Two stocks with
    equal cost but wildly different market values must still be 50/50."""
    view = dashboard(
        [buy("WIN", "100", "100"), buy("LOSE", "100", "100")],
        [instrument("WIN"), instrument("LOSE")],
        [quote("WIN", "1000"), quote("LOSE", "1")],  # 100,000 vs 100 market value
    )
    rows = {r.ticker: r for r in view.stocks}
    assert rows["WIN"].allocation_pct == D("50.00")
    assert rows["LOSE"].allocation_pct == D("50.00")


def test_allocation_survives_total_price_failure():
    """Allocation never touches a price, so it stays correct with no quotes at all."""
    view = dashboard(
        [buy("A", "100", "100"), buy("B", "300", "100")], [instrument("A"), instrument("B")], []
    )
    rows = {r.ticker: r for r in view.stocks}
    assert rows["A"].allocation_pct == D("25.00")
    assert rows["B"].allocation_pct == D("75.00")
    assert rows["A"].market_value is None
    assert view.unpriced == ("A", "B")


# --- FIFO deletion: the decision the doc leaves open ---------------------------------


def test_delete_fewer_units_reduces_position():
    """'If units are less than total units, then the stock should get updated with
    remaining units'."""
    positions = build_positions([buy("A", "100", "100"), sell("A", "40")])
    assert positions["A"].units == D("60")
    assert positions["A"].invested == D("6000")


def test_delete_all_units_removes_stock():
    """'if units are same as total units then stock should be deleted from the portfolio'."""
    positions = build_positions([buy("A", "100", "100"), sell("A", "100")])
    assert "A" not in positions


def test_delete_more_than_held_is_rejected():
    with pytest.raises(InsufficientUnitsError):
        build_positions([buy("A", "100", "100"), sell("A", "101")])


def test_fifo_consumes_oldest_lot_first():
    """Bought 100 @ 100 in January, 100 @ 200 in March. Selling 100 must consume the
    cheap January lot, leaving 100 units at a 20,000 cost basis -- not the 15,000 an
    average-cost method would give."""
    positions = build_positions(
        [
            buy("A", "100", "100", date(2026, 1, 1), seq=1),
            buy("A", "100", "200", date(2026, 3, 1), seq=2),
            sell("A", "100", date(2026, 6, 1)),
        ]
    )
    assert positions["A"].units == D("100")
    assert positions["A"].invested == D("20000")
    assert positions["A"].avg_cost == D("200")


def test_fifo_partial_lot_consumption():
    """Selling 150 eats the whole first lot and half the second."""
    positions = build_positions(
        [
            buy("A", "100", "100", date(2026, 1, 1), seq=1),
            buy("A", "100", "200", date(2026, 3, 1), seq=2),
            sell("A", "150", date(2026, 6, 1)),
        ]
    )
    assert positions["A"].units == D("50")
    assert positions["A"].invested == D("10000")  # 50 @ 200


def test_incremental_upload_appends_to_existing_lots():
    """The doc's 'incremental upload' requirement: the new list appends, it does not
    replace. Two buys of the same stock accumulate."""
    positions = build_positions(
        [
            buy("A", "100", "100", date(2026, 1, 1), seq=1),
            buy("A", "50", "300", date(2026, 5, 1), seq=2),
        ]
    )
    assert positions["A"].units == D("150")
    assert positions["A"].invested == D("25000")  # 10,000 + 15,000


# --- Money is never a float ----------------------------------------------------------


def test_no_float_leaks_into_money():
    view = dashboard(
        [buy("A", "3", "0.1")], [instrument("A")], [quote("A", "0.2")]
    )
    row = view.stocks[0]
    for value in (row.invested, row.market_value, row.pnl, row.allocation_pct):
        assert isinstance(value, Decimal)
    # 3 x 0.1 is exactly 0.30 in Decimal. In float it is 0.30000000000000004.
    assert row.invested == D("0.30")


def test_fractional_units_supported():
    """US fractional shares: 2.5 units at $100."""
    view = dashboard(
        [Transaction("F", Market.US, TxnType.BUY, D("2.5"), D("100"), date(2026, 1, 1))],
        [Instrument("F", Market.US, "Frac Inc", "Information Technology")],
        [quote("F", "120")],
    )
    row = view.stocks[0]
    assert row.units == D("2.5")
    assert row.invested == D("250.00")
    assert row.market_value == D("300.00")


# --- Edge cases ----------------------------------------------------------------------


def test_empty_portfolio():
    view = dashboard([], [], [])
    assert view.stocks == ()
    assert view.sectors == ()
    assert view.totals.invested == D("0.00")
    assert view.totals.market_value is None


def test_partially_priced_sector_flags_the_gap():
    """One of two Auto stocks has no price: the sector total covers only the priced one,
    and says so rather than pretending to be complete."""
    view = dashboard(
        [buy("A", "100", "100"), buy("B", "100", "100")],
        [instrument("A", "Auto"), instrument("B", "Auto")],
        [quote("A", "250")],  # B unpriced
    )
    auto = view.sectors[0]
    assert auto.invested == D("20000.00")  # full cost basis
    assert auto.market_value == D("25000.00")  # only A
    assert auto.unpriced_count == 1
    assert view.unpriced == ("B",)


def test_allocations_sum_to_one_hundred():
    view = dashboard(
        [buy("A", "33", "100"), buy("B", "33", "100"), buy("C", "34", "100")],
        [instrument("A"), instrument("B"), instrument("C")],
        [],
    )
    total = sum(r.allocation_pct for r in view.stocks)
    assert abs(total - D("100")) <= D("0.03")  # rounding slack, 0.01 per row
