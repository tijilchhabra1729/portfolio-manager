"""Recording trades, the recent-trades feed, and the per-market settings -- against a
real Postgres. Prices come from a fake provider and the fundamentals lookup is stubbed, so
nothing reaches the network."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.main import app
from app.core.calculations import realised_pnl
from app.core.sectors import Market
from app.market.cache import PriceService
from app.services import dashboard_service, trade_service
from app.services.trade_service import TradeError
from app.store import repository
from app.store.db import connect
from tests.conftest import TABLES, FakeProvider

D = Decimal
USER = "local"
US = Market.US
TODAY = date.today()
PRICES = {"AAPL": "315", "MSFT": "385"}


class _NoFund:
    def get_fundamentals(self, market, ticker):
        return None


@pytest.fixture(autouse=True)
def _no_lookup(monkeypatch):
    """A hand-entered ticker with a missing name/sector would otherwise ask yfinance."""
    monkeypatch.setattr(trade_service, "default_provider", lambda: _NoFund())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_prices", PriceService(FakeProvider(PRICES)))
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))
    yield TestClient(app)
    with connect() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))


def _record(conn, ticker="AAPL", side="BUY", units="10", price="300", day=TODAY, **kw):
    return trade_service.record_trade(
        conn, USER, US, ticker=ticker, side=side, units=D(units), price=D(price),
        txn_date=day, **kw,
    )


# --- recording a trade (service) ------------------------------------------------------


def test_buy_of_a_new_ticker_creates_its_instrument_and_position(conn):
    r = _record(conn, name="Apple Inc", sector="Information Technology")
    assert r.instrument_created is True
    assert r.units_held == D("10")
    assert r.allocation_pct == D("100.00")  # the only holding
    inst = repository.get_instruments(conn, USER, US)["AAPL"]
    assert inst.name == "Apple Inc"
    assert inst.sector == "Information Technology"


def test_unrecognised_sector_files_under_others(conn):
    _record(conn, name="Apple Inc", sector="Gadgets")
    assert repository.get_instruments(conn, USER, US)["AAPL"].sector == "Others"


def test_sector_resolves_case_and_spacing(conn):
    _record(conn, name="Apple Inc", sector="  information technology ")
    assert repository.get_instruments(conn, USER, US)["AAPL"].sector == "Information Technology"


def test_missing_name_falls_back_to_the_ticker(conn):
    _record(conn, sector="Information Technology")
    assert repository.get_instruments(conn, USER, US)["AAPL"].name == "AAPL"


def test_priced_sell_realises_a_gain_and_reports_the_post_trade_allocation(conn):
    _record(conn, "AAPL", units="10", price="300", name="Apple", sector="Information Technology")
    _record(conn, "MSFT", units="10", price="300", name="Microsoft", sector="Information Technology")
    r = _record(conn, "AAPL", side="SELL", units="5", price="400")
    # 5 * (400 - 300) realised; AAPL keeps 5 @ 300 = 1500 of a 4500 book.
    assert realised_pnl(repository.get_transactions(conn, USER, US)) == D("500")
    assert r.units_held == D("5")
    assert r.allocation_pct == D("33.33")
    assert r.instrument_created is False


def test_sell_more_than_held_is_rejected_and_writes_nothing(conn):
    _record(conn, units="10", name="Apple", sector="Information Technology")
    with pytest.raises(TradeError, match="only 10 held"):
        _record(conn, side="SELL", units="11", price="400")
    assert len(repository.get_transactions(conn, USER, US)) == 1


def test_backdated_sell_before_the_buy_is_rejected(conn):
    _record(conn, units="10", name="Apple", sector="Information Technology", day=TODAY)
    with pytest.raises(TradeError, match="did not yet hold"):
        _record(conn, side="SELL", units="5", price="400", day=TODAY - timedelta(days=1))
    assert len(repository.get_transactions(conn, USER, US)) == 1


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"units": "0"}, "Quantity"),
        ({"price": "0"}, "Price"),
        ({"side": "HOLD"}, "BUY or SELL"),
        ({"day": TODAY + timedelta(days=1)}, "future"),
        ({"ticker": "  "}, "ticker"),
    ],
)
def test_bad_inputs_are_rejected(conn, bad, message):
    with pytest.raises(TradeError, match=message):
        _record(conn, name="Apple", sector="Information Technology", **bad)


# --- the recent-trades feed (service) -------------------------------------------------


def test_feed_is_newest_first_with_as_of_allocation_and_realised_gain(conn):
    _record(conn, "AAPL", units="10", price="300", day=TODAY - timedelta(days=2),
            name="Apple", sector="Information Technology")
    _record(conn, "MSFT", units="10", price="300", day=TODAY - timedelta(days=1),
            name="Microsoft", sector="Information Technology")
    _record(conn, "AAPL", side="SELL", units="5", price="400", day=TODAY)

    feed = trade_service.recent_trades(conn, USER, US)
    assert [(t["kind"], t["ticker"]) for t in feed] == [
        ("sell", "AAPL"), ("buy", "MSFT"), ("buy", "AAPL"),
    ]
    sell, msft, aapl = feed
    assert sell["allocation_pct"] == "33.33" and sell["realised_gain"] == "500.00"
    assert sell["price"] == "400.00" and sell["units"] == "5"
    assert msft["allocation_pct"] == "50.00"  # as of that trade: 3000 of 6000
    assert aapl["allocation_pct"] == "100.00"  # the first and only holding then
    assert aapl["realised_gain"] is None and aapl["name"] == "Apple"


def test_feed_limit_takes_the_newest(conn):
    for i in range(4):
        _record(conn, units="1", day=TODAY - timedelta(days=3 - i),
                name="Apple", sector="Information Technology")
    feed = trade_service.recent_trades(conn, USER, US, limit=2)
    assert len(feed) == 2
    assert feed[0]["date"] == TODAY.isoformat()


def test_a_file_remove_shows_as_removed_not_a_sale(conn):
    from app.core.models import Transaction, TxnType

    _record(conn, units="10", name="Apple", sector="Information Technology")
    # What delete_units writes: a SELL with no price.
    repository.add_transactions(
        conn, USER,
        [Transaction("AAPL", US, TxnType.SELL, D("4"), D(0), TODAY)],
        source_file="delete.xlsx",
    )
    newest = trade_service.recent_trades(conn, USER, US)[0]
    assert newest["kind"] == "removed"
    assert newest["price"] is None and newest["realised_gain"] is None
    assert realised_pnl(repository.get_transactions(conn, USER, US)) == D("0")


def test_empty_ledger_gives_an_empty_feed(conn):
    assert trade_service.recent_trades(conn, USER, US) == []


# --- settings (repository) --------------------------------------------------------------


def test_settings_round_trip(conn):
    assert repository.get_settings(conn, USER, US) is None
    repository.upsert_settings(conn, USER, US, total_investable=D("50000"), options_income=D("100"))
    row = repository.get_settings(conn, USER, US)
    assert row["total_investable"] == D("50000") and row["options_income"] == D("100")
    # Upsert, not insert: a second write updates the same row.
    repository.upsert_settings(conn, USER, US, total_investable=None, options_income=D("250"))
    row = repository.get_settings(conn, USER, US)
    assert row["total_investable"] is None and row["options_income"] == D("250")


# --- the API ----------------------------------------------------------------------------


def _post(client, **body):
    payload = {"ticker": "AAPL", "side": "BUY", "units": "10", "price": "300",
               "date": TODAY.isoformat(), "name": "Apple Inc", "sector": "Information Technology"}
    payload.update(body)
    return client.post("/api/US/trades", json=payload)


def test_api_records_trades_and_the_dashboard_reflects_them(client):
    r = _post(client)
    assert r.status_code == 200, r.text
    assert r.json()["allocation_pct"] == "100.00"
    assert r.json()["instrument_created"] is True

    d = client.get("/api/US/dashboard").json()
    row = d["stocks"][0]
    assert row["ticker"] == "AAPL" and row["avg_cost"] == "300.00" and row["price"] == "315"
    assert row["day_change_pct"] is None  # no earlier snapshot to compare against
    assert d["totals"]["realised_pnl"] == "0.00"

    r = _post(client, side="SELL", units="5", price="400")
    assert r.status_code == 200, r.text
    t = client.get("/api/US/dashboard").json()["totals"]
    # 5 left @300 = 1500 invested; priced 315 -> unrealised 75; realised 500; net 575.
    assert t["realised_pnl"] == "500.00"
    assert t["pnl"] == "75.00"
    assert t["net_pnl"] == "575.00"
    assert t["net_pnl_pct"] == "38.33"

    feed = client.get("/api/US/trades").json()
    assert feed[0]["kind"] == "sell" and feed[0]["realised_gain"] == "500.00"
    assert feed[1]["kind"] == "buy"


def test_api_rejects_a_bad_trade_with_a_readable_message(client):
    r = _post(client, side="SELL", units="5")
    assert r.status_code == 400
    assert "only 0 held" in r.json()["detail"]


def test_api_settings_drive_the_cash_position(client):
    r = client.put("/api/US/settings", json={"total_investable": "50000", "options_income": "1000"})
    assert r.status_code == 200
    assert r.json() == {"total_investable": "50000.00", "options_income": "1000.00"}
    assert client.get("/api/US/settings").json()["options_income"] == "1000.00"

    _post(client)  # invest 3000
    t = client.get("/api/US/dashboard").json()["totals"]
    assert t["investable"] == "50000.00"
    assert t["cash"] == "47000.00"
    assert t["cash_pct"] == "94.00"  # cash / investable
    assert t["options_income"] == "1000.00"
    assert t["options_income_pct"] == "33.33"  # 1000 / 3000 invested

    # A partial update keeps what it didn't mention.
    r = client.put("/api/US/settings", json={"options_income": "2000"})
    assert r.json() == {"total_investable": "50000.00", "options_income": "2000.00"}

    r = client.put("/api/US/settings", json={"total_investable": "-1"})
    assert r.status_code == 400
