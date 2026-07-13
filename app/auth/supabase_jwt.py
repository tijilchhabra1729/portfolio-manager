"""Supabase Auth.

The browser logs in against Supabase directly and sends us the resulting JWT. We only
verify it -- there is no user table of our own, no password handling, no session store.
Every row is then scoped by the token's `sub` claim.

Supabase signs those tokens one of two ways, and a project can be on either:

  * **Asymmetric** (ECC/RSA) -- the current default. Supabase publishes the public half
    at a JWKS endpoint; we fetch it and verify. Nothing secret is involved.
  * **Shared secret** (HS256) -- the legacy scheme, using the project's "JWT Secret".

Which one we expect is decided by *configuration*, never by reading the token's own `alg`
header. Trusting that header is the classic algorithm-confusion attack: an attacker takes
the public key (which is public), sets `alg: HS256`, and signs a token of their own
choosing using that public key as the HMAC secret. A verifier that believes the header
would happily accept it. So: if a JWT secret is configured we accept HS256 and nothing
else; otherwise we accept only the asymmetric algorithms.

With AUTH_ENABLED=false the whole thing short-circuits to a fixed local user, so
development needs no login while user_id behaves exactly as it will in production.
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import LOCAL_USER_ID, settings

bearer = HTTPBearer(auto_error=False)

ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    url = settings().supabase_url.rstrip("/")
    return jwt.PyJWKClient(
        f"{url}/auth/v1/.well-known/jwks.json",
        # Supabase's auth endpoints reject an unauthenticated request, so even fetching
        # the *public* signing key needs the publishable key attached.
        headers={"apikey": settings().supabase_anon_key},
        cache_keys=True,  # cached, so this is not a network hop per request
    )


def _key_and_algorithms(token: str) -> tuple[object, list[str]]:
    secret = settings().supabase_jwt_secret
    if secret:
        return secret, ["HS256"]
    if not settings().supabase_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth is on but neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set.",
        )
    return _jwks().get_signing_key_from_jwt(token).key, ASYMMETRIC_ALGORITHMS


def _decode(token: str) -> str:
    try:
        key, algorithms = _key_and_algorithms(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,  # from config, never from the token's own header
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
