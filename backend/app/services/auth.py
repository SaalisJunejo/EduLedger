"""Authentication service (Argon2id password hashing and JWT route protection).

Provides password hashing and verification using the Argon2id algorithm
via ``argon2-cffi``, matching the security requirements in PRD Section 3,
along with the ``require_auth`` decorator for JWT route protection.
"""

from functools import wraps
from typing import Callable, Iterable

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import current_app, g, jsonify, request

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a plain text password using Argon2id."""
    return _hasher.hash(plain)


def verify_password(plain: str, hash: str) -> bool:
    """Verify a plain text password against an Argon2id hash string.

    Returns True if the password matches the hash, False otherwise.
    """
    if not plain or not hash:
        return False
    try:
        return _hasher.verify(hash, plain)
    except (VerificationError, InvalidHashError):
        return False


def require_auth(roles: Iterable[str] | str | None = None) -> Callable:
    """Flask route decorator requiring a valid JWT Bearer token.

    Validates the ``Authorization: Bearer <token>`` header.
    - Missing header or token -> 401 {check: "auth", error: "missing_token"}
    - Expired token -> 401 {check: "auth", error: "token_expired"}
    - Invalid or malformed token -> 401 {check: "auth", error: "invalid_token"}
    - Role mismatch (if roles specified) -> 403 {check: "auth", error: "insufficient_role"}

    On success, attaches ``g.user_id`` and ``g.role`` (and ``g.auth_claims``).
    """
    fn_arg = None
    if callable(roles):
        fn_arg = roles
        roles = None

    if isinstance(roles, str):
        allowed_roles = {roles}
    elif roles is not None:
        allowed_roles = set(roles)
    else:
        allowed_roles = None

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from ..api.errors import api_error

            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return api_error(
                    "auth",
                    "missing_token",
                    "Authorization header with Bearer token is required",
                    status=401,
                )

            token = auth_header[7:].strip()
            if not token:
                return api_error(
                    "auth",
                    "missing_token",
                    "Bearer token missing from Authorization header",
                    status=401,
                )

            try:
                claims = jwt.decode(
                    token,
                    current_app.config["JWT_SECRET"],
                    algorithms=["HS256"],
                )
            except jwt.ExpiredSignatureError:
                return api_error(
                    "auth",
                    "token_expired",
                    "Authentication token has expired",
                    status=401,
                )
            except jwt.InvalidTokenError:
                return api_error(
                    "auth",
                    "invalid_token",
                    "Authentication token is invalid or malformed",
                    status=401,
                )

            user_id = claims.get("user_id")
            role = claims.get("role")

            if allowed_roles is not None and role not in allowed_roles:
                return api_error(
                    "auth",
                    "insufficient_role",
                    f"Access forbidden: role '{role}' is not authorized",
                    status=403,
                )

            g.user_id = user_id
            g.role = role
            g.auth_claims = claims

            return fn(*args, **kwargs)

        return wrapper

    if fn_arg is not None:
        return decorator(fn_arg)
    return decorator
