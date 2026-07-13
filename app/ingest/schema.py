"""The shape of the intake workbook, in one place.

Both the template generator and the reader import from here, so a column can never be
added to the sample file without the parser learning about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Only these four are needed. A broker export (Zerodha and friends) carries no stock name
# and no purchase date -- it reports an already-averaged position, not a lot history --
# and demanding them would mean hand-editing every download.
REQUIRED_HOLDINGS_COLUMNS: tuple[str, ...] = (
    "Ticker",
    "Units",
    "Price Per Unit",
    "Sector",
)

# Broker exports use their own headers. Matched case-insensitively, punctuation ignored,
# so "Quantity Available", "quantity available" and "Qty. Available" all resolve.
COLUMN_ALIASES: dict[str, str] = {
    "symbol": "Ticker",
    "tradingsymbol": "Ticker",
    "trading symbol": "Ticker",
    "scrip": "Ticker",
    "instrument": "Ticker",
    "name": "Stock Name",
    "company": "Stock Name",
    "company name": "Stock Name",
    "quantity available": "Units",
    "quantity": "Units",
    "qty": "Units",
    "qty available": "Units",
    "shares": "Units",
    "holdings": "Units",
    "average price": "Price Per Unit",
    "avg price": "Price Per Unit",
    "avg cost": "Price Per Unit",
    "average cost": "Price Per Unit",
    "buy average": "Price Per Unit",
    "buy avg": "Price Per Unit",
    "cost price": "Price Per Unit",
    "industry": "Sector",
    "date": "Purchase Date",
    "trade date": "Purchase Date",
    "buy date": "Purchase Date",
}

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


def _as_dicts(rows: list[RowError]) -> list[dict]:
    return [
        {"sheet": e.sheet, "row": e.row, "column": e.column, "message": e.message}
        for e in rows
    ]


@dataclass
class ValidationReport:
    """Errors are collected across the whole file rather than raised on the first bad
    cell, so the user fixes their spreadsheet in one pass instead of ten uploads.

    Warnings are things we handled but you should know about -- chiefly a sector we could
    not place, which becomes "Others". The upload still succeeds; it just does not do so
    silently. Reclassifying a holding without telling anyone is how a sector allocation
    goes quietly wrong.
    """

    errors: list[RowError]
    warnings: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dicts(self) -> list[dict]:
        return _as_dicts(self.errors)

    def warnings_as_dicts(self) -> list[dict]:
        return _as_dicts(self.warnings)
