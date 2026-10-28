"""Rolling 6-digit attendance code (Phase 1.1, PRD v2 section 4.1).

The code is derived deterministically from a session's CURRENT nonce -
HMAC-SHA256(SECRET_KEY, nonce) mod 10**6, zero-padded - so it needs no
stored state and rotates in lockstep with the QR. Students who cannot scan
the QR type the code instead; /attendance/scan then runs the same checks.

Brute-force guard: wrong codes are counted per (student, session) and the
code path is refused once MAX_WRONG_CODE_ATTEMPTS is reached. The counter
lives in this process's memory only - with several workers (or after a
restart) each process counts separately. Redis replaces it in Phase 1.2a.
"""

import hashlib
import hmac
import threading

from flask import current_app

CODE_DIGITS = 6
MAX_WRONG_CODE_ATTEMPTS = 5

_attempts_lock = threading.Lock()
_wrong_attempts: dict[tuple[int, int], int] = {}


def derive_code(nonce: str, secret_key: str) -> str:
    """The 6-digit code for `nonce` under `secret_key`."""
    digest = hmac.new(secret_key.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest, "big") % 10**CODE_DIGITS
    return f"{value:0{CODE_DIGITS}d}"


def current_code(session) -> str:
    """The code matching the session's current nonce."""
    return derive_code(session.current_nonce, current_app.config["SECRET_KEY"])


def _normalize(submitted) -> str | None:
    """Accept "012345" or 12345; anything that is not 6 digits is None."""
    if isinstance(submitted, bool):
        return None
    if isinstance(submitted, int):
        return f"{submitted:0{CODE_DIGITS}d}" if 0 <= submitted < 10**CODE_DIGITS else None
    if isinstance(submitted, str):
        text = submitted.strip()
        if len(text) == CODE_DIGITS and text.isascii() and text.isdigit():
            return text
    return None


def code_matches(session, submitted) -> bool:
    """Constant-time comparison of a submitted code with the current one."""
    normalized = _normalize(submitted)
    if normalized is None:
        return False
    return hmac.compare_digest(normalized, current_code(session))


def too_many_attempts(student_id: int, session_id: int) -> bool:
    """True once the student has used up their wrong codes for this session."""
    with _attempts_lock:
        return _wrong_attempts.get((student_id, session_id), 0) >= MAX_WRONG_CODE_ATTEMPTS


def record_wrong_attempt(student_id: int, session_id: int) -> int:
    """Count one wrong (or expired) code; returns the new total."""
    with _attempts_lock:
        key = (student_id, session_id)
        _wrong_attempts[key] = _wrong_attempts.get(key, 0) + 1
        return _wrong_attempts[key]
