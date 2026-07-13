"""The agent layer's front door.

This returns [] today and will keep returning [] until an agent writes to the insights
table. It exists now, wired end to end and rendered by the UI, so that shipping the
first agent is a matter of writing rows -- not of touching the API or the frontend.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.supabase_jwt import current_user
from app.core.sectors import Market
from app.store import repository
from app.store.db import connect

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/{market}/insights")
def insights(market: Market, user_id: str = Depends(current_user)):
    with connect() as conn:
        rows = repository.get_insights(conn, user_id, market)
    return [
        {
            "id": r["id"],
            "severity": r["severity"],
            "title": r["title"],
            "body": r["body"],
            "related_ticker": r["related_ticker"],
            "related_sector": r["related_sector"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
