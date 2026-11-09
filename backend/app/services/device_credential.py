"""Device-bound biometric credential (Phase 1.7a, decision D4).

The Expo student app cannot prove a biometric match to the server - it can
only report "the OS prompt succeeded on this device". So the trust anchor is
a secret the phone's OS releases *only* after that prompt:

1. POST /api/v1/students/device/enroll      -> the server mints a 32-byte
   secret, stores it on the student's device binding and returns it ONCE.
   The app keeps it in the Keychain / Keystore behind biometric access
   control.
2. GET  /api/v1/students/identity/challenge -> a random, single-use
   challenge with a short TTL.
3. POST /api/v1/students/verify-identity-device sends
   ``HMAC_SHA256(secret, challenge + ":" + device_id)`` as hex; the server
   recomputes it and compares in constant time, then mints the same
   30-second verification token the face path issues.

Challenges live in Redis so every server process shares them; if Redis is
unreachable the module warns once and falls back to this process's memory
(single-process demo still gets single-use semantics). A spent challenge
leaves a short-lived "used" marker so a replay can be reported as
`challenge_used` rather than looking like a plain expiry.

The secret itself is stored server-side in plain form - an FYP
simplification, noted in PROJECT_CONTEXT.md's known gaps.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time

import redis
from flask import current_app

logger = logging.getLogger(__name__)

# 32 random bytes for both the secret and the challenge (256-bit).
SECRET_BYTES = 32
CHALLENGE_BYTES = 32

# How long a spent challenge is remembered after its own TTL, so a replay is
# named precisely instead of being reported as an expiry.
USED_MARKER_GRACE_SECONDS = 120

# consume_challenge() outcomes; the two failures double as API error codes.
CHALLENGE_OK = "ok"
CHALLENGE_EXPIRED = "challenge_expired"
CHALLENGE_USED = "challenge_used"

_store_lock = threading.Lock()
# In-memory fallback: key -> unix time it stops counting.
_open_challenges: dict[str, float] = {}
_used_challenges: dict[str, float] = {}

_redis_lock = threading.Lock()
# client: the live connection; warned: Redis already proved unreachable, so
# stop retrying (and stop logging) until the process restarts.
_redis_state: dict = {"url": None, "client": None, "warned": False}


def _open_key(student_id: int, challenge: str) -> str:
    return f"eduledger:identity_challenge:{student_id}:{challenge}"


def _used_key(student_id: int, challenge: str) -> str:
    return f"eduledger:identity_challenge_used:{student_id}:{challenge}"


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
                    "Redis at %s is unreachable - identity challenges fall back to "
                    "this process's memory",
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
                "Redis dropped an identity challenge - falling back to this "
                "process's memory"
            )


def _prune(now: float) -> None:
    """Drop stale in-memory entries. Call with `_store_lock` held."""
    for store in (_open_challenges, _used_challenges):
        for key in [key for key, expires_at in store.items() if expires_at <= now]:
            store.pop(key, None)


def _random_token(size: int) -> str:
    """`size` random bytes as unpadded base64url (URL- and JSON-safe)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).decode("ascii").rstrip("=")


def ttl_seconds() -> int:
    """Challenge lifetime from config (IDENTITY_CHALLENGE_TTL_SECONDS)."""
    return int(current_app.config["IDENTITY_CHALLENGE_TTL_SECONDS"])


def generate_secret() -> str:
    """A fresh device secret: 32 random bytes as base64url."""
    return _random_token(SECRET_BYTES)


def expected_response(secret: str, challenge: str, device_id: str) -> str:
    """``HMAC_SHA256(secret, challenge + ":" + device_id)`` as lowercase hex."""
    message = f"{challenge}:{device_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def response_matches(secret: str, challenge: str, device_id: str, submitted) -> bool:
    """Constant-time comparison of the submitted HMAC with the expected one."""
    if not isinstance(submitted, str):
        return False
    return hmac.compare_digest(
        expected_response(secret, challenge, device_id),
        submitted.strip().lower(),
    )


def issue_challenge(student_id: int) -> tuple[str, int]:
    """Mint a single-use challenge for `student_id`; returns (challenge, ttl)."""
    challenge = _random_token(CHALLENGE_BYTES)
    ttl = ttl_seconds()

    client = _redis_client()
    if client is not None:
        try:
            client.set(_open_key(student_id, challenge), "open", ex=ttl)
            return challenge, ttl
        except Exception:
            _redis_failed()

    now = time.time()
    with _store_lock:
        _prune(now)
        _open_challenges[_open_key(student_id, challenge)] = now + ttl
    return challenge, ttl


def consume_challenge(student_id: int, challenge: str) -> str:
    """Spend a challenge once: CHALLENGE_OK / _USED / _EXPIRED.

    A challenge issued to another student is simply not found (the store is
    keyed per student), and an unknown challenge is indistinguishable from an
    expired one - both answer CHALLENGE_EXPIRED.
    """
    if not isinstance(challenge, str) or not challenge.strip():
        return CHALLENGE_EXPIRED
    challenge = challenge.strip()
    open_key = _open_key(student_id, challenge)
    used_key = _used_key(student_id, challenge)
    used_for = ttl_seconds() + USED_MARKER_GRACE_SECONDS

    client = _redis_client()
    if client is not None:
        try:
            if client.delete(open_key):
                client.set(used_key, "used", ex=used_for)
                return CHALLENGE_OK
            return CHALLENGE_USED if client.exists(used_key) else CHALLENGE_EXPIRED
        except Exception:
            _redis_failed()

    now = time.time()
    with _store_lock:
        _prune(now)  # expired challenges are gone before the lookup
        if _open_challenges.pop(open_key, None) is not None:
            _used_challenges[used_key] = now + used_for
            return CHALLENGE_OK
        if used_key in _used_challenges:
            return CHALLENGE_USED
    return CHALLENGE_EXPIRED
