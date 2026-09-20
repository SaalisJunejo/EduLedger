"""Substitute authorization JWTs (Module 3 - Faculty Workload Ledger).

Minted by POST /api/v1/faculty/substitute/issue after the scheduled
professor names a substitute for one specific class, and redeemed via
POST /api/v1/faculty/substitute/redeem within SUBSTITUTE_TOKEN_MINUTES
(default 30 min). The `purpose` claim prevents these tokens from being
replayed as any other token type (login, attendance verification, ...)
exactly like the Module 1 verification tokens - the two services share the
JWT_SECRET and the same mint/verify shape on purpose.
"""

import time

import jwt
from flask import current_app

PURPOSE = "faculty_substitute"


class SubstituteTokenError(Exception):
    """Raised when a substitute token is invalid (generic)."""

    code = "substitute_token_invalid"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class SubstituteTokenExpired(SubstituteTokenError):
    """Raised when a substitute token's 30-minute window has passed."""

    code = "substitute_token_expired"


def mint(
    issued_by: int, substitute_id: int, class_id: int, ttl_minutes: int | None = None
) -> tuple[str, float]:
    """Mint a substitute token; returns ``(token, expires_at_unix)``.

    `ttl_minutes` defaults to SUBSTITUTE_TOKEN_MINUTES from config
    (tests may pass a custom - even negative - value).
    """
    if ttl_minutes is None:
        ttl_minutes = int(current_app.config["SUBSTITUTE_TOKEN_MINUTES"])
    now = int(time.time())
    expires_at = now + ttl_minutes * 60
    payload = {
        "sub": str(substitute_id),  # the named substitute (only they may redeem)
        "purpose": PURPOSE,
        "class_id": class_id,  # the one specific class being covered
        "issued_by": issued_by,  # the scheduled professor
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")
    return token, float(expires_at)


def verify(token: str) -> dict:
    """Decode and validate a substitute token; returns its claims.

    Raises SubstituteTokenExpired / SubstituteTokenError with demo-friendly
    messages on any failure.
    """
    try:
        claims = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise SubstituteTokenExpired(
            "substitute token expired - ask the professor to issue a fresh one"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise SubstituteTokenError("substitute token is invalid or malformed") from exc

    if claims.get("purpose") != PURPOSE:
        raise SubstituteTokenError(
            "token was not issued for substitute authorization",
            code="substitute_token_wrong_purpose",
        )
    return claims
