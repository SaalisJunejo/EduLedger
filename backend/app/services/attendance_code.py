"""Rolling 6-digit attendance code (Phase 1.1, PRD v2 section 4.1).

The code is derived deterministically from a session's CURRENT nonce -
HMAC-SHA256(SECRET_KEY, nonce) mod 10**6, zero-padded - so it needs no
stored state and rotates in lockstep with the QR. Students who cannot scan
the QR type the code instead; /attendance/scan then runs the same checks.
The code may also be typed alone (Phase 1.1b): `sessions_showing()` finds
the live session whose current code it is.

Brute-force guard: with a session_id, wrong codes are counted per
(student, session) and the code path is refused once
MAX_WRONG_CODE_ATTEMPTS is reached. Code-only scans guess across every live
session at once, so they are counted per student instead
(MAX_WRONG_CODE_ONLY_ATTEMPTS within WRONG_CODE_ONLY_TTL_SECONDS). The
counters live in Redis (INCR + EXPIRE, Phase 1.2a) so every server process
shares them; if Redis is unreachable the module warns once and falls back to
this process's memory, which still blocks a single-process brute force.
"""

import hashlib
import hmac
import logging
import threading
import time

import redis
from flask import current_app

from ..models import AttendanceSession
from ..util import utcnow

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
MAX_WRONG_CODE_ATTEMPTS = 5
# Attempts are forgotten an hour after the first one - longer than a class
# meeting, so the counter cannot be reset by simply waiting one out.
WRONG_CODE_TTL_SECONDS = 3600

# Code-only scans (no session_id): one counter per student across sessions.
MAX_WRONG_CODE_ONLY_ATTEMPTS = 5
WRONG_CODE_ONLY_TTL_SECONDS = 600

_attempts_lock = threading.Lock()
_wrong_attempts: dict[tuple[int, int], int] = {}
# student id -> (wrong code-only attempts, time.monotonic() they expire at)
_wrong_code_only_attempts: dict[int, tuple[int, float]] = {}

_redis_lock = threading.Lock()
# client: the live connection; warned: Redis already proved unreachable, so
# stop retrying (and stop logging) until the process restarts.
_redis_state: dict = {"url": None, "client": None, "warned": False}


def _redis_key(student_id: int, session_id: int) -> str:
    return f"eduledger:wrong_code:{session_id}:{student_id}"


def _code_only_redis_key(student_id: int) -> str:
    return f"eduledger:wrong_code_only:{student_id}"


def _redis_client():
    """A ready Redis client, or None when Redis is unset or unreachable."""
    url = current_app.config.get("REDIS_URL")
    if not url:
        return None
    with _redis_lock:
        if _redis_state["url"] != url:  # config changed (tests, new app)
            _redis_state.update(url=url, client=None, warned=False)
        if _redis_state["client"] is None and not _redis_state["warned"]:
            try:
                client = redis.Redis.from_url(
                    url,
                    socket_timeout=0.5,
                    socket_connect_timeout=0.5,
                    decode_responses=True,
                )
                client.ping()
                _redis_state["client"] = client
            except Exception:
                _redis_state["warned"] = True
                logger.warning(
                    "Redis at %s is unreachable - wrong attendance-code attempts fall "
                    "back to this process's memory",
                    url,
                )
        return _redis_state["client"]


def _redis_failed() -> None:
    """Drop a client that died mid-operation; the fallback takes over."""
    with _redis_lock:
        if _redis_state["client"] is not None:
            _redis_state["client"] = None
            _redis_state["warned"] = True
            logger.warning(
                "Redis dropped the wrong attendance-code counter - falling back to "
                "this process's memory"
            )


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


def sessions_showing(submitted) -> list[AttendanceSession]:
    """Every live session whose current code is `submitted` right now.

    Same window rules as the QR nonce check: the session is active, was not
    ended by its conductor, and its nonce (so its code) has not expired.
    """
    normalized = _normalize(submitted)
    if normalized is None:
        return []
    secret_key = current_app.config["SECRET_KEY"]
    live = AttendanceSession.query.filter(
        AttendanceSession.is_active.is_(True),
        AttendanceSession.ended_at.is_(None),
        AttendanceSession.nonce_expires_at > utcnow(),
    ).all()
    return [
        session
        for session in live
        if hmac.compare_digest(normalized, derive_code(session.current_nonce, secret_key))
    ]


def too_many_attempts(student_id: int, session_id: int) -> bool:
    """True once the student has used up their wrong codes for this session."""
    client = _redis_client()
    if client is not None:
        try:
            counted = client.get(_redis_key(student_id, session_id))
            return int(counted or 0) >= MAX_WRONG_CODE_ATTEMPTS
        except Exception:
            _redis_failed()
    with _attempts_lock:
        return _wrong_attempts.get((student_id, session_id), 0) >= MAX_WRONG_CODE_ATTEMPTS


def record_wrong_attempt(student_id: int, session_id: int) -> int:
    """Count one wrong (or expired) code; returns the new total."""
    client = _redis_client()
    if client is not None:
        try:
            key = _redis_key(student_id, session_id)
            total = int(client.incr(key))
            if total == 1:  # first miss starts the window
                client.expire(key, WRONG_CODE_TTL_SECONDS)
            return total
        except Exception:
            _redis_failed()
    with _attempts_lock:
        key = (student_id, session_id)
        _wrong_attempts[key] = _wrong_attempts.get(key, 0) + 1
        return _wrong_attempts[key]


def _code_only_in_memory(student_id: int, now: float) -> int:
    """The in-memory code-only count; drops an expired window. Hold the lock."""
    count, expires_at = _wrong_code_only_attempts.get(student_id, (0, 0.0))
    if expires_at <= now:
        _wrong_code_only_attempts.pop(student_id, None)
        return 0
    return count


def too_many_code_only_attempts(student_id: int) -> bool:
    """True once the student has used up their wrong code-only scans."""
    client = _redis_client()
    if client is not None:
        try:
            counted = client.get(_code_only_redis_key(student_id))
            return int(counted or 0) >= MAX_WRONG_CODE_ONLY_ATTEMPTS
        except Exception:
            _redis_failed()
    with _attempts_lock:
        return _code_only_in_memory(student_id, time.monotonic()) >= MAX_WRONG_CODE_ONLY_ATTEMPTS


def record_wrong_code_only_attempt(student_id: int) -> int:
    """Count one code-only scan that matched no live session; returns the total."""
    client = _redis_client()
    if client is not None:
        try:
            key = _code_only_redis_key(student_id)
            total = int(client.incr(key))
            if total == 1:  # first miss starts the window
                client.expire(key, WRONG_CODE_ONLY_TTL_SECONDS)
            return total
        except Exception:
            _redis_failed()
    with _attempts_lock:
        now = time.monotonic()
        count = _code_only_in_memory(student_id, now)
        expires_at = (
            _wrong_code_only_attempts[student_id][1] if count else now + WRONG_CODE_ONLY_TTL_SECONDS
        )
        _wrong_code_only_attempts[student_id] = (count + 1, expires_at)
        return count + 1
