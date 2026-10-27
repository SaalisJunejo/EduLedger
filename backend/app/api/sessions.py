"""Attendance session endpoints (Module 1 - Zero-Proxy Attendance Engine).

- POST /api/v1/session/start       - instructor opens a session for a class
- GET  /api/v1/session/<id>/qr     - projector-facing QR (rotating nonce)

The QR endpoint is designed to be polled every 1-2 seconds: responses carry
`Cache-Control: no-store`, always reflect the latest nonce, and include the
nonce expiry so the projector can re-render before it changes.
"""

import base64
import io
import json

import qrcode
from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import AttendanceSession, ScheduledClass
from ..services.auth import require_auth
from ..services.timetable import start_gate
from ..util import iso_z, utcnow
from . import api_v1
from .errors import api_error


def _qr_png_data_uri(payload: str) -> str:
    """Render `payload` as a base64-encoded PNG data URI."""
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _refresh_if_stale(session: AttendanceSession) -> None:
    """Rotate the nonce inline when it expired and the scheduler hasn't yet.

    This is a fallback, not the primary mechanism - the background job in
    `app/scheduler.py` rotates nonces every NONCE_ROTATION_SECONDS. Because
    rotation only touches expired nonces, this lazy path can never fight the
    scheduler or skip a beat.
    """
    if session.nonce_is_expired():
        session.rotate_nonce(ttl_seconds=current_app.config["NONCE_ROTATION_SECONDS"])
        db.session.commit()


@api_v1.post("/session/start")
@require_auth(roles=["FACULTY"])
def start_session():
    """Start a new attendance session for a class.

    Body: ``{"class_id": 7}`` (ScheduledClass.id, not a course code).
    The authenticated faculty must own that class and pass the schedule gate
    before any existing session is closed or a rotating nonce is created.
    """
    body = request.get_json(silent=True) or {}
    value = body.get("class_id")
    if isinstance(value, str) and len(value.strip()) <= 10 and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2147483647:
        return api_error("request", "missing_fields", "class_id must be a positive ScheduledClass.id", 400)
    scheduled_class = db.session.get(ScheduledClass, value)
    if scheduled_class is None:
        return api_error("class", "class_not_found", f"scheduled class {value} does not exist", 404)
    if scheduled_class.professor_id != g.user_id:
        return api_error("professor", "not_class_professor", "class is not owned by the authenticated faculty", 403)
    denied = start_gate(scheduled_class)
    if denied is not None:
        return denied
    class_id = str(scheduled_class.id)

    now = utcnow()
    ttl_seconds = current_app.config["NONCE_ROTATION_SECONDS"]

    # One live session per class: close the previous one, if any.
    previous = AttendanceSession.query.filter_by(class_id=class_id, is_active=True).all()
    for session in previous:
        session.is_active = False

    session = AttendanceSession(class_id=class_id)
    session.rotate_nonce(now=now, ttl_seconds=ttl_seconds)

    db.session.add(session)
    db.session.commit()

    return (
        jsonify(
            {
                "session": session.to_dict(),
                "qr_poll_url": f"/api/v1/session/{session.id}/qr",
                "rotation_seconds": ttl_seconds,
            }
        ),
        201,
    )


@api_v1.get("/session/<int:session_id>/qr")
@require_auth(roles=["FACULTY"])
def session_qr(session_id):
    """Current QR code for a session, as a base64 PNG data URI.

    The QR encodes the compact JSON payload ``{"session_id": <id>,
    "nonce": "<current nonce>"}``. Poll this endpoint every 1-2 seconds and
    swap the ``<img src>`` to the returned ``qr_image`` to keep the projected
    QR current. Unknown sessions return 404; sessions that were replaced or
    closed return 409 so the projector can stop polling.
    """
    session = db.session.get(AttendanceSession, session_id)
    if session is None:
        return jsonify({"error": f"session {session_id} not found"}), 404
    if not session.is_active:
        return jsonify({"error": f"session {session_id} is no longer active"}), 409

    _refresh_if_stale(session)

    payload = json.dumps(
        {"session_id": session.id, "nonce": session.current_nonce},
        separators=(",", ":"),
    )

    response = jsonify(
        {
            "session_id": session.id,
            "class_id": session.class_id,
            "current_nonce": session.current_nonce,
            "nonce_expires_at": iso_z(session.nonce_expires_at),
            "server_time": iso_z(utcnow()),
            "rotation_seconds": current_app.config["NONCE_ROTATION_SECONDS"],
            "qr_image": _qr_png_data_uri(payload),
        }
    )
    # The projector must never see a cached QR - each poll returns the
    # nonce that is live *right now*.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
