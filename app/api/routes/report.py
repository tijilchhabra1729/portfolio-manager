"""Weekly health report: read the latest, or force-generate one."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.supabase_jwt import current_user
from app.core.sectors import Market
from app.services import analysis_service
from app.store.db import connect

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/{market}/report")
def latest(market: Market, user_id: str = Depends(current_user)):
    with connect() as conn:
        report = analysis_service.latest_report(conn, user_id, market)
    return report or {}


@router.post("/{market}/report")
def generate(market: Market, user_id: str = Depends(current_user)):
    """The Generate-report button. Overwrites this week's report in place."""
    with connect() as conn:
        return analysis_service.generate_report(conn, user_id, market)
