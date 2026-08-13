"""The agent layer's front door: read insights, dismiss one.

The GET was live and empty through phase 1; now the agents write rows and they appear
with no change here. The dismiss endpoint is owner-scoped in the repository — one user
cannot dismiss another's insight even by guessing the id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth.supabase_jwt import current_user
from app.core.sectors import Market
from app.store import agent_repo, repository
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


@router.post("/insights/{insight_id}/dismiss")
def dismiss(insight_id: int, user_id: str = Depends(current_user)):
    with connect() as conn:
        ok = agent_repo.dismiss_insight(conn, user_id, insight_id)
    if not ok:
        # Either it doesn't exist or it isn't this user's — same 404 either way, so a
        # probe can't distinguish "not yours" from "not found".
        raise HTTPException(404, "insight not found")
    return {"ok": True}
