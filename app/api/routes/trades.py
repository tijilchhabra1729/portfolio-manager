"""Trades and per-market settings: the hand-entered side of the ledger.

- POST /{market}/trades   record one priced BUY or SELL (the "Record trade" dialog)
- GET  /{market}/trades   the recent buy & sell feed
- GET/PUT /{market}/settings   the investable pot and options income

Money crosses the wire as strings, as everywhere else in this API.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.supabase_jwt import current_user
from app.core.calculations import q_money
from app.core.sectors import Market
from app.services import trade_service
from app.store import repository
from app.store.db import connect

router = APIRouter(prefix="/api", tags=["trades"])

MAX_FEED = 50


class TradeIn(BaseModel):
    ticker: str
    side: str  # BUY | SELL
    units: Decimal
    price: Decimal
    date: dt.date
    name: str | None = None
    sector: str | None = None


class SettingsIn(BaseModel):
    total_investable: Decimal | None = None
    options_income: Decimal | None = None


def _settings_out(row: dict | None) -> dict:
    # NUMERIC(20,4) comes back carrying four decimals; money on the wire is two.
    row = row or {}
    investable = row.get("total_investable")
    return {
        "total_investable": None if investable is None else str(q_money(investable)),
        "options_income": str(q_money(row.get("options_income") or Decimal(0))),
    }


@router.get("/{market}/trades")
def trades(market: Market, limit: int = 10, user_id: str = Depends(current_user)):
    with connect() as conn:
        return trade_service.recent_trades(
            conn, user_id, market, limit=max(1, min(limit, MAX_FEED))
        )


@router.post("/{market}/trades")
def record(market: Market, body: TradeIn, user_id: str = Depends(current_user)):
    try:
        with connect() as conn:
            result = trade_service.record_trade(
                conn,
                user_id,
                market,
                ticker=body.ticker,
                side=body.side,
                units=body.units,
                price=body.price,
                txn_date=body.date,
                name=body.name,
                sector=body.sector,
            )
    except trade_service.TradeError as exc:
        raise HTTPException(400, str(exc)) from None
    return {
        "ticker": result.ticker,
        "side": result.side,
        "units": str(result.units),
        "price": str(result.price),
        "date": result.txn_date.isoformat(),
        "allocation_pct": str(result.allocation_pct),
        "units_held": str(result.units_held),
        "instrument_created": result.instrument_created,
    }


@router.get("/{market}/settings")
def get_settings(market: Market, user_id: str = Depends(current_user)):
    with connect() as conn:
        row = repository.get_settings(conn, user_id, market)
    return _settings_out(row)


@router.put("/{market}/settings")
def put_settings(market: Market, body: SettingsIn, user_id: str = Depends(current_user)):
    if body.total_investable is not None and body.total_investable < 0:
        raise HTTPException(400, "Total investable amount can't be negative.")
    if body.options_income is not None and body.options_income < 0:
        raise HTTPException(400, "Options income can't be negative.")
    with connect() as conn:
        # A partial body keeps whatever it didn't mention.
        current = repository.get_settings(conn, user_id, market) or {}
        investable = (
            body.total_investable
            if body.total_investable is not None
            else current.get("total_investable")
        )
        options = (
            body.options_income
            if body.options_income is not None
            else (current.get("options_income") or Decimal(0))
        )
        repository.upsert_settings(
            conn, user_id, market, total_investable=investable, options_income=options
        )
        row = repository.get_settings(conn, user_id, market)
    return _settings_out(row)
