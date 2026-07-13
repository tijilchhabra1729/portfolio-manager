"""Supabase Auth.

The browser logs in against Supabase directly and sends the resulting JWT here. We only
verify it -- there is no user table of our own, no password handling, and no session
store. Every row is then scoped by the `sub` claim.

With AUTH_ENABLED=false the whole thing short-circuits to a fixed local user, so
development needs no login while user_id still behaves exactly as it will in production.
"""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import LOCAL_USER_ID, settings

bearer = HTTPBearer(auto_error=False)


def _decode(token: str) -> str:
    try:
        claims = jwt.decode(
            token,
            settings().supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}"
        ) from exc

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no subject."
        )
    return user_id


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if not settings().auth_enabled:
        return LOCAL_USER_ID
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )
    return _decode(credentials.credentials)


def require_refresh_token(request: Request) -> None:
    """Guards the cron endpoint. GitHub Actions has no browser session, so it presents a
    shared bearer token instead."""
    header = request.headers.get("authorization", "")
    presented = header.removeprefix("Bearer ").strip()
    if not presented or presented != settings().refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad refresh token."
        )
