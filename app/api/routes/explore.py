"""Explore: analyse any ticker's fundamentals, held or not."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException

from app.auth.supabase_jwt import current_user
from app.core.sectors import Market
from app.services import analysis_service
from app.store.db import connect

router = APIRouter(prefix="/api", tags=["explore"])

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-&]{1,20}$")


@router.get("/{market}/explore/{ticker}")
def explore(
    market: Market,
    ticker: str,
    refresh: bool = False,
    user_id: str = Depends(current_user),
):
    if not TICKER_RE.match(ticker):
        raise HTTPException(400, "Not a valid ticker symbol.")

    with connect() as conn:
        result = analysis_service.explore(conn, user_id, market, ticker, force=refresh)

    return {
        "ticker": result.ticker,
        "market": market.value,
        "fundamentals": result.fundamentals,
        "read": result.read,
        "source": result.source,
        "cached": result.cached,
        "usage": {"used": result.used, "limit": result.limit},
        "error": result.error,
    }
