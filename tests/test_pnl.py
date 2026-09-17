"""Realised P&L, the fuller totals (net, options, cash), and the per-row extras (avg
cost, day change). Pure maths over the ledger -- no database, no prices fetched."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.core.calculations import (
    build_dashboard,
    build_positions,
    realised_pnl,
    replay_ledger,
)
from app.core.models import Instrument, Quote, Transaction, TxnType
from app.core.sectors import Market

D = Decimal
NOW = datetime(2026, 7, 13)


def buy(t, units, price, day=date(2026, 1, 1), seq=0):
    return Transaction(t, Market.INDIA, TxnType.BUY, D(units), D(price), day, seq)


def sell_at(t, units, price, day=date(2026, 6, 1), seq=99):
    return Transaction(t, Market.INDIA, TxnType.SELL, D(units), D(price), day, seq)


def remove(t, units, day=date(2026, 6, 1), seq=99):
    """The file-based remove: a SELL with no price."""
    return Transaction(t, Market.INDIA, TxnType.SELL, D(units), D(0), day, seq)


def inst(t):
    return Instrument(t, Market.INDIA, f"{t} Ltd", "Auto")


def quote(t, price):
    return Quote(t, D(price), NOW)


def _view(txns, price, **extras):
    return build_dashboard(
        Market.INDIA,
        build_positions(txns),
        {"XX": inst("XX")},
        {"XX": quote("XX", price)},
        now=NOW,
        **extras,
    )


# --- realised P&L via FIFO ------------------------------------------------------------


def test_realised_gain_on_a_partial_lot():
    # 100 @ 100; sell 40 @ 150 -> gain 40 * (150 - 100) = 2000; 60 remain costing 6000.
    r = replay_ledger([buy("XX", "100", "100"), sell_at("XX", "40", "150")])
    assert r.realised_pnl == D("2000")
    assert len(r.realised) == 1
    assert r.realised[0].cost_basis == D("4000")
    assert r.positions["XX"].units == D("60")
    assert r.positions["XX"].invested == D("6000")


def test_realised_gain_consumes_oldest_lot_first():
    # 100@100 (Jan), 100@200 (Feb); sell 150 @ 250:
    # 100*(250-100) + 50*(250-200) = 15000 + 2500.
    txns = [
        buy("XX", "100", "100", date(2026, 1, 1), 1),
        buy("XX", "100", "200", date(2026, 2, 1), 2),
        sell_at("XX", "150", "250"),
    ]
    assert realised_pnl(txns) == D("17500")


def test_realised_loss_is_negative():
    assert realised_pnl([buy("XX", "10", "100"), sell_at("XX", "10", "80")]) == D("-200")


def test_price_zero_remove_realises_nothing_but_still_consumes():
    r = replay_ledger([buy("XX", "100", "100"), remove("XX", "40")])
    assert r.realised == ()
    assert r.realised_pnl == D("0")
    assert r.positions["XX"].units == D("60")  # the lot was still consumed


def test_build_positions_is_unchanged_by_the_refactor():
    txns = [buy("XX", "100", "100"), sell_at("XX", "40", "150")]
    assert build_positions(txns) == replay_ledger(txns).positions


# --- the fuller totals ----------------------------------------------------------------


def test_net_pnl_is_realised_plus_unrealised_plus_options():
    txns = [buy("XX", "100", "100"), sell_at("XX", "40", "150")]  # realised 2000; 60 left
    t = _view(txns, "120", realised=realised_pnl(txns), options_income=D("500")).totals
    assert t.invested == D("6000.00")
    assert t.pnl == D("1200.00")  # unrealised: 60 * (120 - 100)
    assert t.realised_pnl == D("2000.00")
    assert t.options_income == D("500.00")
    assert t.net_pnl == D("3700.00")  # 2000 + 1200 + 500
    # every percentage is against invested (6000)
    assert t.realised_pnl_pct == D("33.33")
    assert t.options_income_pct == D("8.33")
    assert t.net_pnl_pct == D("61.67")


def test_cash_position_is_investable_minus_invested_and_pct_of_investable():
    t = _view([buy("XX", "100", "100")], "100", investable=D("50000")).totals
    assert t.investable == D("50000.00")
    assert t.cash == D("40000.00")
    assert t.cash_pct == D("80.00")  # cash / investable -- not / invested


def test_over_invested_cash_goes_negative():
    t = _view([buy("XX", "100", "100")], "100", investable=D("8000")).totals  # invested 10000
    assert t.cash == D("-2000.00")
    assert t.cash_pct == D("-25.00")


def test_totals_without_extras_are_defaulted():
    t = _view([buy("XX", "100", "100")], "100").totals
    assert t.realised_pnl == D("0")
    assert t.options_income == D("0")
    assert t.realised_pnl_pct == D("0")
    assert t.investable is None and t.cash is None and t.cash_pct is None


def test_net_pnl_unknown_when_book_is_unpriced():
    view = build_dashboard(
        Market.INDIA, build_positions([buy("XX", "100", "100")]), {"XX": inst("XX")}, {},
        now=NOW, realised=D("500"), options_income=D("100"),
    )
    t = view.totals
    assert t.pnl is None
    assert t.net_pnl is None and t.net_pnl_pct is None  # an unknown leg makes the sum unknown
    assert t.realised_pnl == D("500.00")  # the known legs still show


# --- per-row extras -------------------------------------------------------------------


def test_avg_cost_is_the_cost_basis_of_remaining_units():
    txns = [
        buy("XX", "100", "100", date(2026, 1, 1), 1),
        buy("XX", "100", "200", date(2026, 2, 1), 2),
        sell_at("XX", "100", "250"),
    ]
    # FIFO consumed the 100@100 lot; 100@200 remain -> avg cost 200.
    assert _view(txns, "210").stocks[0].avg_cost == D("200.00")


def test_day_change_against_previous_close():
    row = _view([buy("XX", "100", "100")], "110", prev_closes={"XX": D("100")}).stocks[0]
    assert row.day_change_pct == D("10.00")


def test_day_change_is_none_without_a_usable_previous_close():
    assert _view([buy("XX", "100", "100")], "110").stocks[0].day_change_pct is None
    assert (
        _view([buy("XX", "100", "100")], "110", prev_closes={"XX": D("0")}).stocks[0].day_change_pct
        is None
    )
