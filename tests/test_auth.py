"""Token verification, including the attack it must not fall for."""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.auth import supabase_jwt
from app.config import LOCAL_USER_ID, Settings

USER = "8f14e45f-ceea-467a-9575-2c5d4a1b2c3d"
SECRET = "a-legacy-shared-jwt-secret"


def configure(monkeypatch, **overrides) -> None:
    base = dict(auth_enabled=True, supabase_url="https://proj.supabase.co")
    monkeypatch.setattr(supabase_jwt, "settings", lambda: Settings(**base | overrides))
    supabase_jwt._jwks.cache_clear()


def hs256(claims: dict, secret: str = SECRET) -> str:
    return jwt.encode(claims, secret, algorithm="HS256")


def claims(**extra) -> dict:
    return {"sub": USER, "aud": "authenticated", **extra}


# --- legacy shared-secret projects ---------------------------------------------------


def test_hs256_token_is_accepted_when_a_jwt_secret_is_configured(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret=SECRET)
    assert supabase_jwt._decode(hs256(claims())) == USER


def test_token_signed_with_the_wrong_secret_is_rejected(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret=SECRET)
    with pytest.raises(HTTPException) as exc:
        supabase_jwt._decode(hs256(claims(), secret="not-the-secret"))
    assert exc.value.status_code == 401


def test_token_without_a_subject_is_rejected(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret=SECRET)
    with pytest.raises(HTTPException):
        supabase_jwt._decode(hs256({"aud": "authenticated"}))


def test_wrong_audience_is_rejected(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret=SECRET)
    with pytest.raises(HTTPException):
        supabase_jwt._decode(hs256(claims(aud="some-other-service")))


# --- the algorithm-confusion attack --------------------------------------------------


def test_hs256_is_refused_on_an_asymmetric_project(monkeypatch):
    """The attack this code is shaped to prevent.

    On a project using asymmetric signing keys, the public key is *public* -- anyone can
    fetch it from the JWKS endpoint. An attacker takes that public key, uses it as an
    HMAC secret to sign a token claiming to be any user they like, and sets alg=HS256. A
    verifier that reads the algorithm out of the token's own header would fetch the same
    public key and validate it happily.

    We never read alg from the token. With no JWT secret configured, only ES256/RS256 are
    accepted, so a forged HS256 token cannot be verified at all -- regardless of what key
    it was signed with.
    """
    configure(monkeypatch, supabase_jwt_secret="")  # asymmetric project

    public_key_as_if_it_were_a_secret = "-----BEGIN PUBLIC KEY-----fake-----END-----"
    forged = hs256(claims(), secret=public_key_as_if_it_were_a_secret)

    called = False

    def spy(token):  # noqa: ANN001
        nonlocal called
        called = True
        raise AssertionError("should never reach key lookup for an HS256 token")

    monkeypatch.setattr(supabase_jwt, "_jwks", lambda: type("K", (), {"get_signing_key_from_jwt": staticmethod(spy)})())

    with pytest.raises((HTTPException, AssertionError)):
        supabase_jwt._decode(forged)


def test_asymmetric_token_is_accepted_via_jwks(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret="")

    private_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(claims(), private_key, algorithm="ES256")

    monkeypatch.setattr(
        supabase_jwt,
        "_jwks",
        lambda: type(
            "K",
            (),
            {
                "get_signing_key_from_jwt": staticmethod(
                    lambda _t: type("S", (), {"key": private_key.public_key()})()
                )
            },
        )(),
    )
    assert supabase_jwt._decode(token) == USER


# --- the switch ----------------------------------------------------------------------


def test_auth_disabled_falls_back_to_the_local_user(monkeypatch):
    monkeypatch.setattr(supabase_jwt, "settings", lambda: Settings(auth_enabled=False))
    assert supabase_jwt.current_user(credentials=None) == LOCAL_USER_ID


def test_auth_enabled_requires_a_token(monkeypatch):
    configure(monkeypatch, supabase_jwt_secret=SECRET)
    with pytest.raises(HTTPException) as exc:
        supabase_jwt.current_user(credentials=None)
    assert exc.value.status_code == 401
