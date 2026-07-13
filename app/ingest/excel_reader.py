"""Parses and validates the intake workbook.

Two rules govern this module:

1. Numbers become Decimal via str(), never Decimal(float). openpyxl hands back floats,
   and Decimal(2450.5) captures the binary approximation whereas Decimal("2450.5") is
   exact. This is the seam where float error would otherwise enter the system.
2. Every bad cell in the file is reported, not just the first. The caller then commits
   nothing unless the report is clean -- a half-applied portfolio upload is worse than a
   rejected one.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.core.sectors import Market, is_valid_sector, spec
from app.ingest.schema import (
    DELETIONS_COLUMNS,
    DELETIONS_SHEET,
    HOLDINGS_COLUMNS,
    HOLDINGS_SHEETS,
    DeletionRow,
    HoldingRow,
    RowError,
    ValidationReport,
)

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-&]{0,19}$")
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y")

Source = Path | BytesIO | bytes


def _open(source: Source) -> Any:
    if isinstance(source, bytes):
        source = BytesIO(source)
    return load_workbook(source, data_only=True, read_only=False)


def _header_map(sheet: Worksheet) -> dict[str, int]:
    header = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return {
        str(name).strip(): idx
        for idx, name in enumerate(header)
        if name is not None and str(name).strip()
    }


def _is_blank(values: tuple) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in values)


class _RowParser:
    """Reads one row, appending to the shared error list instead of raising, so a single
    pass surfaces every problem in the file."""

    def __init__(self, sheet: str, row: int, errors: list[RowError]) -> None:
        self.sheet = sheet
        self.row = row
        self.errors = errors
        self.failed = False

    def _fail(self, column: str, message: str) -> None:
        self.errors.append(RowError(self.sheet, self.row, column, message))
        self.failed = True

    def text(self, column: str, value: Any) -> str:
        if value is None or not str(value).strip():
            self._fail(column, "Required.")
            return ""
        return str(value).strip()

    def ticker(self, column: str, value: Any) -> str:
        raw = self.text(column, value)
        if not raw:
            return ""
        symbol = raw.upper().replace(" ", "")
        if not TICKER_RE.match(symbol):
            self._fail(column, f"'{raw}' is not a valid ticker symbol.")
            return ""
        return symbol

    def positive_decimal(self, column: str, value: Any) -> Decimal:
        if value is None or (isinstance(value, str) and not value.strip()):
            self._fail(column, "Required.")
            return Decimal(0)
        try:
            # str() first: Decimal(2450.5) would bake in the float approximation.
            number = Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            self._fail(column, f"'{value}' is not a number.")
            return Decimal(0)
        if number <= 0:
            self._fail(column, "Must be greater than zero.")
            return Decimal(0)
        return number

    def past_date(self, column: str, value: Any) -> date:
        parsed: date | None = None
        if isinstance(value, datetime):
            parsed = value.date()
        elif isinstance(value, date):
            parsed = value
        elif value is not None and str(value).strip():
            raw = str(value).strip()
            for fmt in DATE_FORMATS:
                try:
                    parsed = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue

        if parsed is None:
            self._fail(column, f"'{value}' is not a date (use YYYY-MM-DD).")
            return date.today()
        if parsed > date.today():
            self._fail(column, "Purchase date is in the future.")
        return parsed

    def sector(self, column: str, value: Any, market: Market) -> str:
        raw = self.text(column, value)
        if not raw:
            return ""
        if not is_valid_sector(market, raw):
            allowed = ", ".join(spec(market).sectors)
            self._fail(column, f"'{raw}' is not a {market.value} sector. Allowed: {allowed}")
            return ""
        return raw

    def market(self, column: str, value: Any) -> Market | None:
        raw = self.text(column, value)
        if not raw:
            return None
        try:
            return Market(raw.upper())
        except ValueError:
            self._fail(column, f"'{raw}' is not a market. Use INDIA or US.")
            return None


def _missing_columns(
    sheet: Worksheet, sheet_name: str, required: tuple[str, ...], errors: list[RowError]
) -> dict[str, int] | None:
    columns = _header_map(sheet)
    missing = [c for c in required if c not in columns]
    if missing:
        errors.append(
            RowError(sheet_name, 1, ", ".join(missing), "Missing required column(s).")
        )
        return None
    return columns


def read_holdings(source: Source) -> tuple[list[HoldingRow], ValidationReport]:
    """Read both holdings sheets. The sheet a row lives on determines its market."""
    workbook = _open(source)
    errors: list[RowError] = []
    rows: list[HoldingRow] = []
    seen_any_sheet = False

    for market, sheet_name in HOLDINGS_SHEETS.items():
        if sheet_name not in workbook.sheetnames:
            continue
        seen_any_sheet = True
        sheet = workbook[sheet_name]
        columns = _missing_columns(sheet, sheet_name, HOLDINGS_COLUMNS, errors)
        if columns is None:
            continue

        for row_idx, values in enumerate(
            sheet.iter_rows(min_row=2, values_only=True), start=2
        ):
            if _is_blank(values):
                continue

            def cell(name: str) -> Any:
                idx = columns[name]
                return values[idx] if idx < len(values) else None

            parser = _RowParser(sheet_name, row_idx, errors)
            row = HoldingRow(
                market=market,
                name=parser.text("Stock Name", cell("Stock Name")),
                ticker=parser.ticker("Ticker", cell("Ticker")),
                units=parser.positive_decimal("Units", cell("Units")),
                price_per_unit=parser.positive_decimal(
                    "Price Per Unit", cell("Price Per Unit")
                ),
                purchase_date=parser.past_date("Purchase Date", cell("Purchase Date")),
                sector=parser.sector("Sector", cell("Sector"), market),
            )
            if not parser.failed:
                rows.append(row)

    if not seen_any_sheet:
        expected = " or ".join(HOLDINGS_SHEETS.values())
        errors.append(RowError("(workbook)", 0, "sheets", f"No {expected} sheet found."))

    return rows, ValidationReport(errors)


def read_deletions(source: Source) -> tuple[list[DeletionRow], ValidationReport]:
    workbook = _open(source)
    errors: list[RowError] = []
    rows: list[DeletionRow] = []

    if DELETIONS_SHEET not in workbook.sheetnames:
        errors.append(
            RowError("(workbook)", 0, "sheets", f"No {DELETIONS_SHEET} sheet found.")
        )
        return rows, ValidationReport(errors)

    sheet = workbook[DELETIONS_SHEET]
    columns = _missing_columns(sheet, DELETIONS_SHEET, DELETIONS_COLUMNS, errors)
    if columns is None:
        return rows, ValidationReport(errors)

    for row_idx, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        # The Deletions sheet has a help note in column E; ignore anything past the
        # three real columns when deciding whether the row is empty.
        if _is_blank(tuple(values[i] for i in columns.values())):
            continue

        def cell(name: str) -> Any:
            idx = columns[name]
            return values[idx] if idx < len(values) else None

        parser = _RowParser(DELETIONS_SHEET, row_idx, errors)
        market = parser.market("Market", cell("Market"))
        ticker = parser.ticker("Ticker", cell("Ticker"))
        units = parser.positive_decimal("Units", cell("Units"))
        if not parser.failed and market is not None:
            rows.append(DeletionRow(market=market, ticker=ticker, units=units))

    return rows, ValidationReport(errors)
