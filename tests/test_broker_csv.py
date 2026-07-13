"""Broker CSV import.

The real file has four columns and none of our names:

    Symbol, Sector, Quantity Available, Average Price

No stock name, no purchase date, and -- critically -- nothing anywhere saying which
market it is for. The .xlsx template encodes the market in its sheet names; a flat CSV
cannot, so the market has to come from the upload form.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.sectors import Market
from app.ingest.excel_reader import read_holdings

D = Decimal

BROKER = b"""Symbol,Sector,Quantity Available,Average Price
RELIANCE,Energy,50,1180.00
HDFCBANK,Financial services,100,890.50
INFY,IT,75,1210.00
ITC,FMCG,400,305.00
"""


def read(content: bytes, market=Market.INDIA, filename="holdings.csv"):
    return read_holdings(content, filename=filename, market=market)


def test_reads_the_brokers_own_column_names():
    rows, report = read(BROKER)
    assert report.ok, report.as_dicts()
    assert len(rows) == 4

    reliance = next(r for r in rows if r.ticker == "RELIANCE")
    assert reliance.units == D("50")            # from "Quantity Available"
    assert reliance.price_per_unit == D("1180.00")  # from "Average Price"
    assert reliance.sector == "Energy"
    assert reliance.market is Market.INDIA


def test_average_price_stays_an_exact_decimal():
    """890.50 must not become the float 890.5000000000001 on its way through csv."""
    rows, _ = read(BROKER)
    hdfc = next(r for r in rows if r.ticker == "HDFCBANK")
    assert isinstance(hdfc.price_per_unit, Decimal)
    assert hdfc.units * hdfc.price_per_unit == D("89050.00")


def test_missing_stock_name_falls_back_to_the_symbol():
    """A broker export has no company name. That is cosmetic -- do not reject the row."""
    rows, _ = read(BROKER)
    assert next(r for r in rows if r.ticker == "INFY").name == "INFY"


def test_missing_purchase_date_is_dated_today():
    """'Average Price' means the broker already blended the lots. There is no purchase
    history to record, so FIFO has nothing to order and today's date costs nothing."""
    rows, _ = read(BROKER)
    assert all(r.purchase_date == date.today() for r in rows)


def test_a_csv_without_a_market_is_rejected_not_guessed():
    """The one thing a flat file genuinely cannot tell us. Guessing at it from the ticker
    would silently file US stocks into the India portfolio."""
    rows, report = read(BROKER, market=None)
    assert not report.ok
    assert rows == []
    assert "market" in report.errors[0].column
    assert "India or US" in report.errors[0].message


def test_market_comes_from_the_upload_form():
    rows, report = read(
        b"Symbol,Sector,Quantity Available,Average Price\nAAPL,Information Technology,25,245.00\n",
        market=Market.US,
    )
    assert report.ok, report.as_dicts()
    assert rows[0].market is Market.US
    assert rows[0].sector == "Information Technology"


# --- sector aliasing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "broker_sector,expected",
    [
        # Zerodha has no "Banking" -- banks live under Financial services, and NBFC is
        # broken out on its own.
        ("Banks", "Financial services"),
        ("BANKING", "Financial services"),
        ("Insurance", "Financial services"),
        ("NBFC", "NBFC"),
        ("Pharmaceuticals", "Healthcare"),
        ("Pharma", "Healthcare"),
        ("Information Technology", "IT"),
        ("Software", "Software services"),
        ("Auto", "Automobile"),
        ("Auto Components", "Auto ancillary"),
        ("Oil and Gas", "Energy"),
        ("Power", "Energy"),
        ("Steel", "Metals"),
        ("Consumer Goods", "FMCG"),
        ("Realty", "Real estate"),
        ("Cement", "Building materials"),
        ("Capital Goods", "Engineering & capital goods"),
    ],
)
def test_broker_sector_names_are_mapped_onto_the_zerodha_taxonomy(broker_sector, expected):
    """Rejecting a file because the broker writes 'Banks' and Zerodha writes 'Financial
    services' would be pedantry."""
    csv = f"Symbol,Sector,Quantity Available,Average Price\nX,{broker_sector},1,100\n"
    rows, report = read(csv.encode())
    assert report.ok, report.as_dicts()
    assert rows[0].sector == expected


def test_an_unknown_sector_becomes_others_and_is_reported():
    """Closed taxonomy, soft landing: the row survives, but the reclassification is never
    silent -- a stock quietly moved into a bucket you did not choose is how a sector
    allocation goes wrong without anyone noticing."""
    csv = b"Symbol,Sector,Quantity Available,Average Price\nX,Crypto Mining,1,100\n"
    rows, report = read(csv)

    assert report.ok
    assert rows[0].sector == "Others"
    assert "Crypto Mining" in report.warnings[0].message
    assert "Others" in report.warnings[0].message


def test_a_missing_sector_becomes_others():
    csv = b"Symbol,Sector,Quantity Available,Average Price\nX,,1,100\n"
    rows, report = read(csv)
    assert report.ok
    assert rows[0].sector == "Others"
    assert report.warnings


def test_you_can_opt_into_keeping_your_own_sector_name():
    """'let people specify their own sector name if it is being classified as Others'."""
    csv = b"Symbol,Sector,Quantity Available,Average Price\nX,Renewable Energy,1,100\n"
    rows, report = read_holdings(
        csv, filename="h.csv", market=Market.INDIA, keep_custom_sectors=True
    )
    assert report.ok
    assert rows[0].sector == "Renewable Energy"
    assert "kept as your own" in report.warnings[0].message


def test_excel_saved_csv_with_a_bom_still_parses():
    """Excel prefixes a BOM, which would otherwise glue itself to 'Symbol' and make the
    first column unrecognisable."""
    rows, report = read("﻿".encode() + BROKER)
    assert report.ok, report.as_dicts()
    assert len(rows) == 4


def test_bad_rows_are_reported_with_csv_line_numbers():
    csv = (
        b"Symbol,Sector,Quantity Available,Average Price\n"
        b"RELIANCE,Energy,50,1180.00\n"
        b"HDFCBANK,Financial services,100,890.50\n"
        b"INFY,IT,-5,1210.00\n"
    )
    rows, report = read(csv)
    assert not report.ok
    assert [e.row for e in report.errors] == [4]  # header is row 1
    assert report.errors[0].column == "Units"


def test_the_xlsx_template_still_works_unchanged(sample_workbook):
    """The workbook path must be untouched by any of this: its sheets still decide the
    market, and no `market` argument is needed."""
    rows, report = read_holdings(sample_workbook, filename="portfolio.xlsx")
    assert report.ok, report.as_dicts()
    assert {r.market for r in rows} == {Market.INDIA, Market.US}
    assert len(rows) == 10


# --- broker .xlsx (not just .csv) ----------------------------------------------------


def _broker_xlsx(sheet_name: str = "Holdings") -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["Symbol", "Sector", "Quantity Available", "Average Price"])
    ws.append(["RELIANCE", "Energy", 50, 1180.00])
    ws.append(["HDFCBANK", "Banks", 100, 890.50])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_a_broker_xlsx_with_its_own_sheet_name_is_read():
    """Our template names its sheets India_Holdings / US_Holdings, but that is a
    convenience for carrying the market -- not a requirement. Making someone rename a tab
    before we will read their file would be gatekeeping."""
    rows, report = read_holdings(
        _broker_xlsx("Equity"), filename="kite.xlsx", market=Market.INDIA
    )
    assert report.ok, report.as_dicts()
    assert len(rows) == 2
    assert next(r for r in rows if r.ticker == "HDFCBANK").sector == "Financial services"
    assert all(r.market is Market.INDIA for r in rows)


def test_a_broker_xlsx_without_a_market_is_rejected():
    """Same rule as a CSV: nothing in the file says which market, so we must not guess."""
    rows, report = read_holdings(_broker_xlsx(), filename="kite.xlsx")
    assert not report.ok
    assert rows == []
    assert "does not say which market" in report.errors[0].message


def test_a_workbook_with_no_usable_columns_says_so():
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Foo", "Bar", "Baz"])
    buffer = BytesIO()
    wb.save(buffer)

    rows, report = read_holdings(
        buffer.getvalue(), filename="junk.xlsx", market=Market.INDIA
    )
    assert not report.ok
    assert "columns we need" in report.errors[0].message


def test_our_template_still_ignores_the_market_argument(sample_workbook):
    """The template names its sheets, so it decides per row -- a market passed on the
    form must not override India_Holdings into US."""
    rows, report = read_holdings(
        sample_workbook, filename="portfolio.xlsx", market=Market.US
    )
    assert report.ok, report.as_dicts()
    assert {r.market for r in rows} == {Market.INDIA, Market.US}
