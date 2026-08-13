"""The Briefing tab's endpoints.

Cross-market by design — the orchestrator agent sits above both markets, so a briefing is
one document, and there is no `{market}` in the path (unlike the per-market dashboard,
insights, and report). Both endpoints are owner-scoped through the JWT.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.supabase_jwt import current_user
from app.services import briefing_service

router = APIRouter(prefix="/api", tags=["briefing"])


@router.get("/briefing")
def get_briefing(user_id: str = Depends(current_user)):
    """The latest weekly briefing, or null if none has been generated yet."""
    return briefing_service.latest(user_id)


@router.post("/briefing")
def post_briefing(user_id: str = Depends(current_user)):
    """Generate now. Cooldown-guarded: a recent briefing is returned unchanged with a
    `skipped_reason` rather than re-running the whole agent tree."""
    return briefing_service.generate(user_id)
