"""The shape of the intake workbook, in one place.

Both the template generator and the reader import from here, so a column can never be
added to the sample file without the parser learning about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.sectors import Market

HOLDINGS_SHEETS: dict[Market, str] = {
    Market.INDIA: "India_Holdings",
    Market.US: "US_Holdings",
}
DELETIONS_SHEET = "Deletions"
SECTOR_LIST_SHEET = "_Sectors"

# The doc's four fields (stock name, units, price per unit, purchase date), plus the
# ticker we need to actually price the thing, plus the sector dropdown.
HOLDINGS_COLUMNS: tuple[str, ...] = (
    "Stock Name",
    "Ticker",
    "Units",
    "Price Per Unit",
    "Purchase Date",
    "Sector",
)
DELETIONS_COLUMNS: tuple[str, ...] = ("Market", "Ticker", "Units")


@dataclass(frozen=True)
class HoldingRow:
    market: Market
    name: str
    ticker: str
    units: Decimal
    price_per_unit: Decimal
    purchase_date: date
    sector: str


@dataclass(frozen=True)
class DeletionRow:
    market: Market
    ticker: str
    units: Decimal


@dataclass(frozen=True)
class RowError:
    sheet: str
    row: int  # 1-based, matching the row number the user sees in Excel
    column: str
    message: str


@dataclass
class ValidationReport:
    """Errors are collected across the whole file rather than raised on the first bad
    cell, so the user fixes their spreadsheet in one pass instead of ten uploads."""

    errors: list[RowError]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dicts(self) -> list[dict]:
        return [
            {"sheet": e.sheet, "row": e.row, "column": e.column, "message": e.message}
            for e in self.errors
        ]
