"""Live attendance WebSocket (Phase 1.2a - requirements doc 3.4, PRD v2 4.1).

One room per session, named ``session:<id>``. The instructor's panel joins
it and receives a "student_checked_in" event for every check-in that was
locked on-chain, plus "session_ended" when the conductor closes the session.

Access rules (decision D5 - JWT in the connect handshake):
- `connect` requires a valid, unexpired API JWT in the handshake payload
  ``auth={"token": "<jwt>"}``. Anything else is refused outright, so an
  unauthenticated socket never reaches an event handler.
- `join_session` is allowed for the FACULTY member conducting that session
  (including a substitute who redeemed a token) and for HOD / ADMIN as
  read-only observers. Everyone else gets a "join_error".

Identity is kept in this module's `_connections` map rather than the Flask
session: sockets are per-process anyway, and the map makes the lifetime
explicit (added on connect, dropped on disconnect).

The emit helpers never raise. A check-in that is already locked on-chain
must not fail - or even slow down - because a watcher's transport or the
Redis message queue is unhappy.
"""

import logging
import threading

import jwt
from flask import current_app, request
from flask_socketio import emit, join_room

from .extensions import db, socketio
from .models import AttendanceSession
from .services import live_attendance

logger = logging.getLogger(__name__)

# Roles that may watch any session without conducting it.
OBSERVER_ROLES = {"HOD", "ADMIN"}

_connections_lock = threading.Lock()
_connections: dict[str, dict] = {}


def room_name(session_id) -> str:
    """The room every watcher of one session shares."""
    return f"session:{session_id}"


def _decode_token(token) -> dict | None:
    """Claims of a valid API JWT, or None (missing, malformed, expired)."""
    if not isinstance(token, str) or not token.strip():
        return None
    try:
        return jwt.decode(
            token.strip(),
            current_app.config["JWT_SECRET"],
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:  # also covers ExpiredSignatureError
        return None


def _identity() -> dict | None:
    """The authenticated identity of the socket sending the current event."""
    with _connections_lock:
        return _connections.get(request.sid)


def _join_error(check: str, error: str, message: str) -> None:
    """Reply to a refused join - same {check, error, message} shape as REST."""
    emit("join_error", {"check": check, "error": error, "message": message})


@socketio.on("connect")
def handle_connect(auth=None):
    """Accept the socket only when the handshake carries a valid JWT."""
    token = auth.get("token") if isinstance(auth, dict) else None
    claims = _decode_token(token)
    if claims is None or claims.get("user_id") is None:
        logger.info("Live attendance socket refused: missing or invalid token")
        return False  # Socket.IO reports this to the client as a refusal.

    with _connections_lock:
        _connections[request.sid] = {
            "user_id": claims.get("user_id"),
            "role": claims.get("role"),
        }
    return True


@socketio.on("disconnect")
def handle_disconnect(*args):
    """Forget the socket's identity (the reason argument is version-dependent)."""
    with _connections_lock:
        _connections.pop(request.sid, None)


@socketio.on("join_session")
def handle_join_session(data=None):
    """Subscribe the caller to one session's live roster."""
    identity = _identity()
    if identity is None:
        return _join_error(
            "auth", "not_authenticated", "reconnect with a valid token before joining"
        )

    session_id = data.get("session_id") if isinstance(data, dict) else None
    if isinstance(session_id, str) and session_id.strip().isdigit():
        session_id = int(session_id.strip())
    if isinstance(session_id, bool) or not isinstance(session_id, int):
        return _join_error("request", "missing_fields", "session_id is required")

    session = db.session.get(AttendanceSession, session_id)
    if session is None:
        return _join_error(
            "session", "session_not_found", f"session {session_id} does not exist"
        )

    role = identity.get("role")
    user_id = identity.get("user_id")
    if role not in OBSERVER_ROLES:
        if role != "FACULTY":
            return _join_error(
                "role", "insufficient_role", f"role '{role}' cannot watch a live session"
            )
        if live_attendance.conductor_id(session) != user_id:
            return _join_error(
                "conductor",
                "not_session_conductor",
                f"session {session_id} is not conducted by user {user_id}",
            )

    join_room(room_name(session_id))
    emit(
        "joined",
        {
            "session_id": session_id,
            "room": room_name(session_id),
            "role": role,
            "count": live_attendance.check_in_count(session_id),
            "is_active": session.is_active,
        },
    )


def emit_checked_in(session_id, payload: dict) -> None:
    """Tell a session's watchers that one more student just checked in."""
    _safe_emit("student_checked_in", session_id, payload)


def emit_session_ended(session_id, count: int) -> None:
    """Tell a session's watchers that the conductor closed it."""
    _safe_emit("session_ended", session_id, {"session_id": session_id, "count": count})


def _safe_emit(event: str, session_id, payload: dict) -> None:
    """Emit to one session's room, swallowing (but logging) every failure."""
    try:
        socketio.emit(event, payload, to=room_name(session_id))
    except Exception:
        logger.exception(
            "Live attendance '%s' emit failed for session %s - the check-in itself "
            "is unaffected (is Redis reachable?)",
            event,
            session_id,
        )
