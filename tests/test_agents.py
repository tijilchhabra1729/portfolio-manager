"""Rule agents as pure functions over a crafted DashboardView."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.agents import concentration, drift, small_cap
from tests.factories import sector, stock, view

# --- concentration ------------------------------------------------------------------


def test_stock_over_20pct_is_critical():
    v = view([stock("BIG", "IT", 25), stock("SM", "Auto", 5)], [sector("IT", 25), sector("Auto", 5)])
    drafts = concentration.run(v)
    big = next(d for d in drafts if d.related_ticker == "BIG")
    assert big.severity == "critical"
    assert big.dedupe_key == "stock:BIG"


def test_stock_between_10_and_20_is_warning():
    v = view([stock("MID", "IT", 15)], [sector("IT", 15)])
    assert concentration.run(v)[0].severity == "warning"


def test_stock_under_10pct_is_silent():
    v = view([stock("A", "IT", 8), stock("B", "Auto", 8)], [sector("IT", 8), sector("Auto", 8)])
    assert [d for d in concentration.run(v) if d.dedupe_key.startswith("stock:")] == []


def test_sector_over_25pct_warns():
    v = view([stock("A", "IT", 30)], [sector("IT", 30, count=1)])
    sec = next(d for d in concentration.run(v) if d.dedupe_key == "sector:IT")
    assert sec.severity == "warning"
    assert sec.related_sector == "IT"


def test_sector_over_40pct_is_critical():
    v = view([stock("A", "IT", 45)], [sector("IT", 45)])
    sec = next(d for d in concentration.run(v) if d.dedupe_key == "sector:IT")
    assert sec.severity == "critical"


# --- small cap ----------------------------------------------------------------------


def test_small_cap_over_5pct_warns_and_lists_holdings():
    v = view(
        [stock("SMALL", "IT", 8, invested="8000", cap_class="small"),
         stock("BIG", "Auto", 92, invested="92000", cap_class="large")],
        [sector("IT", 8), sector("Auto", 92)],
        invested="100000",
    )
    drafts = small_cap.run(v)
    assert len(drafts) == 1
    assert drafts[0].severity == "warning"
    assert "SMALL" in drafts[0].body
    assert "8.00%" in drafts[0].title


def test_small_cap_over_10pct_is_critical():
    v = view(
        [stock("S", "IT", 12, invested="12000", cap_class="small"),
         stock("B", "Auto", 88, invested="88000", cap_class="large")],
        [sector("IT", 12), sector("Auto", 88)], invested="100000",
    )
    assert small_cap.run(v)[0].severity == "critical"


def test_no_small_caps_is_silent():
    v = view([stock("B", "IT", 100, cap_class="large")], [sector("IT", 100)])
    assert small_cap.run(v) == []


def test_unknown_cap_excluded_and_reported():
    # An unknown-cap holding is NOT counted as small, but the insight says it was skipped.
    v = view(
        [stock("S", "IT", 6, invested="6000", cap_class="small"),
         stock("U", "Auto", 40, invested="40000", cap_class=None),
         stock("B", "FMCG", 54, invested="54000", cap_class="large")],
        [sector("IT", 6), sector("Auto", 40), sector("FMCG", 54)], invested="100000",
    )
    drafts = small_cap.run(v)
    assert drafts and "1 holding" in drafts[0].body  # the unknown was noted
    assert "6.00%" in drafts[0].title  # only the known small cap counts


# --- drift --------------------------------------------------------------------------


def _hist(days_ago: int, allocations: dict) -> dict:
    return {"captured_on": date.today() - timedelta(days=days_ago), "sector_allocations": allocations}


def test_drift_silent_without_enough_history():
    v = view([stock("A", "IT", 50), stock("B", "Auto", 50)], [sector("IT", 50), sector("Auto", 50)])
    assert drift.run(v, []) == []                       # no history
    assert drift.run(v, [_hist(0, {"IT": "50"})]) == [] # only one snapshot


def test_drift_silent_when_span_too_short():
    # Two snapshots but both from the last two days -- not enough time to call it drift.
    v = view([stock("A", "IT", 60)], [sector("IT", 60)])
    hist = [_hist(1, {"IT": "50"}), _hist(0, {"IT": "60"})]
    assert drift.run(v, hist) == []


def test_drift_fires_on_a_material_move():
    v = view([stock("A", "IT", 60)], [sector("IT", 60)])
    hist = [_hist(7, {"IT": "50"}), _hist(0, {"IT": "60"})]
    drafts = drift.run(v, hist)
    it = next(d for d in drafts if d.related_sector == "IT")
    assert it.dedupe_key == "drift:IT"
    assert "up" in it.title


def test_drift_ignores_small_moves():
    v = view([stock("A", "IT", 52)], [sector("IT", 52)])
    hist = [_hist(7, {"IT": "50"}), _hist(0, {"IT": "52"})]
    assert drift.run(v, hist) == []  # 2 points < 5-point threshold
