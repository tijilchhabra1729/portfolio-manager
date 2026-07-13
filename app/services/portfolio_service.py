"""Upload and delete.

Both operations are validate-then-commit: the entire file is checked before a single row
is written, and any error means nothing is written at all. A half-applied portfolio
upload is worse than a rejected one -- the user would have no way of knowing which half
landed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from sqlalchemy.engine import Connection

from app.core.calculations import build_positions, q_units
from app.core.models import Instrument, Transaction, TxnType
from app.core.sectors import Market
from app.ingest.excel_reader import read_deletions, read_holdings
from app.ingest.schema import RowError
from app.store import repository


class UploadMode(str, Enum):
    REPLACE = "replace"  # the doc's "bulk upload"
    APPEND = "append"  # the doc's "incremental upload"


@dataclass
class UploadResult:
    ok: bool
    mode: UploadMode | None = None
    transactions_added: int = 0
    markets: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    # Sectors we could not place. The upload succeeded, but not silently -- the caller
    # shows these so a holding is never quietly reclassified.
    warnings: list[dict] = field(default_factory=list)


@dataclass
class DeleteResult:
    ok: bool
    removed: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def upload(
    conn: Connection,
    user_id: str,
    content: bytes,
    mode: UploadMode,
    filename: str = "upload.xlsx",
    market: Market | None = None,
    keep_custom_sectors: bool = False,
) -> UploadResult:
    """`market` applies to CSV uploads only. Our workbook decides the market per row from
    the sheet it sits on; a flat broker CSV has no such signal, so the upload form says.

    `keep_custom_sectors` keeps an unrecognised sector under its own name rather than
    filing it under "Others".
    """
    rows, report = read_holdings(
        content,
        filename=filename,
        market=market,
        keep_custom_sectors=keep_custom_sectors,
    )
    if not report.ok:
        return UploadResult(ok=False, errors=report.as_dicts())
    if not rows:
        return UploadResult(
            ok=False,
            errors=[{"sheet": "(workbook)", "row": 0, "column": "-", "message": "No holdings found."}],
        )

    by_market: dict[Market, list] = defaultdict(list)
    for row in rows:
        by_market[row.market].append(row)

    added = 0
    for market, market_rows in by_market.items():
        if mode is UploadMode.REPLACE:
            # Only markets the file actually carries rows for. Uploading an India-only
            # sheet must not silently wipe the US portfolio.
            repository.clear_market(conn, user_id, market)

        repository.upsert_instruments(
            conn,
            user_id,
            [
                Instrument(r.ticker, r.market, r.name, r.sector)
                for r in market_rows
            ],
        )
        added += repository.add_transactions(
            conn,
            user_id,
            [
                Transaction(
                    ticker=r.ticker,
                    market=r.market,
                    txn_type=TxnType.BUY,
                    units=r.units,
                    price_per_unit=r.price_per_unit,
                    txn_date=r.purchase_date,
                )
                for r in market_rows
            ],
            source_file=filename,
        )

    return UploadResult(
        ok=True,
        mode=mode,
        transactions_added=added,
        markets=sorted(m.value for m in by_market),
        warnings=report.warnings_as_dicts(),
    )


def delete_units(
    conn: Connection, user_id: str, content: bytes, filename: str = "delete.xlsx"
) -> DeleteResult:
    """The doc's incremental delete: fewer units than held shrinks the position, all of
    them removes the stock. Units come off the oldest lot first (FIFO)."""
    rows, report = read_deletions(content)
    if not report.ok:
        return DeleteResult(ok=False, errors=report.as_dicts())
    if not rows:
        return DeleteResult(
            ok=False,
            errors=[{"sheet": "Deletions", "row": 0, "column": "-", "message": "No rows to delete."}],
        )

    # Check every row against current holdings before writing any of them. Two rows
    # deleting the same ticker must be checked against their combined total, not
    # independently -- so accumulate as we go.
    errors: list[RowError] = []
    removed: list[dict] = []
    pending: dict[tuple[Market, str], Decimal] = defaultdict(Decimal)
    positions_cache: dict[Market, dict] = {}

    for idx, row in enumerate(rows, start=2):
        if row.market not in positions_cache:
            positions_cache[row.market] = build_positions(
                repository.get_transactions(conn, user_id, row.market)
            )
        positions = positions_cache[row.market]

        position = positions.get(row.ticker)
        if position is None:
            errors.append(
                RowError("Deletions", idx, "Ticker", f"{row.ticker} is not in the {row.market.value} portfolio.")
            )
            continue

        key = (row.market, row.ticker)
        already = pending[key]
        remaining = position.units - already
        if row.units > remaining:
            # q_units so the message reads "only 120 held", not "only 120.000000 held" --
            # NUMERIC(20,6) comes back from Postgres carrying all six decimal places.
            errors.append(
                RowError(
                    "Deletions",
                    idx,
                    "Units",
                    f"Cannot remove {q_units(row.units)} units of {row.ticker}: "
                    f"only {q_units(remaining)} held.",
                )
            )
            continue

        pending[key] += row.units
        left = remaining - row.units
        removed.append(
            {
                "market": row.market.value,
                "ticker": row.ticker,
                "units_removed": str(q_units(row.units)),
                "units_left": str(q_units(left)),
                "position_closed": left == 0,
            }
        )

    if errors:
        return DeleteResult(
            ok=False,
            errors=[
                {"sheet": e.sheet, "row": e.row, "column": e.column, "message": e.message}
                for e in errors
            ],
        )

    # A SELL carries today's date, so it always sorts after the buys it consumes.
    today = date.today()
    for (market, ticker), units in pending.items():
        repository.add_transactions(
            conn,
            user_id,
            [
                Transaction(
                    ticker=ticker,
                    market=market,
                    txn_type=TxnType.SELL,
                    units=units,
                    price_per_unit=Decimal(0),
                    txn_date=today,
                )
            ],
            source_file=filename,
        )

    return DeleteResult(ok=True, removed=removed)
