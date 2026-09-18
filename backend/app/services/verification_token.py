"""Short-lived JWTs proving a live biometric verification (Module 1).

Minted by POST /api/v1/attendance/verify-identity after a successful face
match or WebAuthn check, and consumed by POST /api/v1/attendance/scan within
VERIFICATION_TOKEN_SECONDS (default 30 s). The `purpose` claim prevents these
tokens from ever being replayed as login tokens once the auth layer lands.
"""

import time

import jwt
from flask import current_app

PURPOSE = "attendance_verification"


class VerificationTokenError(Exception):
    """Raised when a verification token is invalid (generic)."""

    code = "verification_token_invalid"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class VerificationTokenExpired(VerificationTokenError):
    """Raised when a verification token's 30-second window has passed."""

    code = "verification_token_expired"


def mint(student_id: int, method: str, device_id: str | None = None, ttl_seconds: int | None = None) -> tuple[str, float]:
    """Mint a verification token; returns ``(token, expires_at_unix)``.

    `ttl_seconds` defaults to VERIFICATION_TOKEN_SECONDS from config
    (tests may pass a custom - even negative - value).
    """
    if ttl_seconds is None:
        ttl_seconds = int(current_app.config["VERIFICATION_TOKEN_SECONDS"])
    now = int(time.time())
    expires_at = now + ttl_seconds
    payload = {
        "sub": str(student_id),
        "method": method,  # "face" or "webauthn"
        "purpose": PURPOSE,
        "device_id": device_id,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")
    return token, float(expires_at)


def verify(token: str) -> dict:
    """Decode and validate a verification token; returns its claims.

    Raises VerificationTokenExpired / VerificationTokenError with demo-friendly
    messages on any failure.
    """
    try:
        claims = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise VerificationTokenExpired(
            "verification token expired - re-run identity verification"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise VerificationTokenError(
            "verification token is invalid or malformed"
        ) from exc

    if claims.get("purpose") != PURPOSE:
        raise VerificationTokenError(
            "token was not issued for attendance verification", code="verification_token_wrong_purpose"
        )
    return claims
