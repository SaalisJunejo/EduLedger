"""Identity verification + attendance scan endpoints (Module 1).

- POST /api/v1/attendance/verify-identity - face match (or WebAuthn stub) -> 30s JWT
- POST /api/v1/attendance/scan            - the full check-in: token, nonce
  (or the 6-digit numeric code, with or without a session_id), device,
  on-chain lock. Checks run in a fixed order (a) to (d) and every failure
  response names the check that failed, for demo debugging.
"""

from datetime import datetime, timezone

from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import AttendanceSession, Student
from ..services import attendance_code, blockchain, live_attendance
from ..services.auth import require_auth
from ..services.face_embedding import (
    EmbeddingMismatchError,
    FaceImageError,
    compare_embeddings,
    embed_face_image,
)
from ..services.verification_token import VerificationTokenError, mint, verify
from ..services.webauthn import WebAuthnError, verify_assertion
from ..sockets import emit_checked_in
from ..util import iso_z, utcnow
from .errors import api_error
from . import api_v1


def _iso_from_unix(timestamp: float) -> str:
    """Format a unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _session_from_code(student_id: int, numeric_code):
    """Resolve a code typed without a session_id to the one session showing it.

    Returns ``(session, None)`` or ``(None, error_response)``. Wrong codes
    are counted per student (not per session); an ambiguous code is not the
    student's fault, so it is not counted.
    """
    if attendance_code.too_many_code_only_attempts(student_id):
        return None, api_error(
            "rate_limit",
            "too_many_attempts",
            "too many wrong codes - scan the QR instead, or wait a few minutes",
            429,
        )
    matches = attendance_code.sessions_showing(numeric_code)
    if not matches:
        attendance_code.record_wrong_code_only_attempt(student_id)
        return None, api_error(
            "session_nonce",
            "code_mismatch",
            "the code does not match any live session's current code "
            "(it may have rotated - read the projector again)",
        )
    if len(matches) > 1:
        return None, api_error(
            "session_nonce",
            "ambiguous_code",
            "Two classes show the same code right now - scan the QR or try again in a few seconds",
            409,
        )
    return matches[0], None


@api_v1.post("/attendance/verify-identity")
@require_auth(roles=["STUDENT"])
def verify_identity():
    """Verify the student's identity and mint a short-lived verification token.

    Body (either modality):
    - Face:    ``{"face_image": "<base64>"}``
    - Finger:  ``{"device_id": "...", "webauthn_assertion": {...}}``
      (simplified stub - see services/webauthn.py)

    Identity is derived from the authenticated student token (g.user_id).
    The submitted face image is embedded and compared (cosine similarity)
    against the student's stored embedding; on a match (>=
    FACE_MATCH_THRESHOLD) a JWT valid for VERIFICATION_TOKEN_SECONDS
    (default 30 s) is returned. The token only proves identity - the actual
    check-in happens in /attendance/scan.
    """
    body = request.get_json(silent=True) or {}
    student_id = g.user_id
    face_image = body.get("face_image")
    device_id = body.get("device_id")
    assertion = body.get("webauthn_assertion")

    if not face_image and not assertion:
        return api_error(
            "identity",
            "missing_fields",
            "provide either face_image (base64) or webauthn_assertion",
        )

    student = db.session.get(Student, student_id)
    if student is None:
        return api_error(
            "identity", "student_not_found", f"student {student_id} does not exist", 404
        )

    if face_image:
        try:
            candidate = embed_face_image(str(face_image))
        except FaceImageError as exc:
            return api_error("identity", "invalid_face_image", str(exc))

        stored = student.face_embedding
        if not stored:
            return api_error(
                "identity",
                "face_not_enrolled",
                f"student {student_id} has no face embedding - enroll via "
                "/api/v1/student/enroll-face",
                409,
            )

        threshold = current_app.config["FACE_MATCH_THRESHOLD"]
        try:
            similarity = compare_embeddings(stored, candidate)
        except EmbeddingMismatchError as exc:
            return api_error("identity", "embedding_provider_mismatch", str(exc))

        if similarity < threshold:
            return api_error(
                "identity",
                "identity_verification_failed",
                f"face similarity {similarity:.4f} is below the threshold {threshold}",
                401,
                similarity=round(similarity, 4),
                threshold=threshold,
            )

        token, expires_at = mint(student.id, "face")
        return jsonify(
            {
                "verification_token": token,
                "method": "face",
                "similarity": round(similarity, 4),
                "threshold": threshold,
                "expires_at": _iso_from_unix(expires_at),
                "expires_in": current_app.config["VERIFICATION_TOKEN_SECONDS"],
            }
        )

    # Fingerprint (WebAuthn) path - simplified stub.
    try:
        verify_assertion(student, device_id, assertion)
    except WebAuthnError as exc:
        return api_error("identity", exc.code, exc.message, exc.status)

    token, expires_at = mint(student.id, "webauthn", device_id=str(device_id).strip())
    return jsonify(
        {
            "verification_token": token,
            "method": "webauthn",
            "expires_at": _iso_from_unix(expires_at),
            "expires_in": current_app.config["VERIFICATION_TOKEN_SECONDS"],
        }
    )


@api_v1.post("/attendance/scan")
@require_auth(roles=["STUDENT"])
def scan():
    """Full attendance check-in: validate everything, then lock on-chain.

    Body: ``{"verification_token": "...", "device_id": "...",
    "session_id": 1, "nonce": "<scanned from the QR>"}``, or the same with
    ``"numeric_code": "123456"`` (typed from the projector) instead of
    ``nonce``. The code may also be sent without ``session_id``: the session
    is then the one live session currently showing that code (Phase 1.1b).

    Validation order (each failure response names its check):
    (a) verification_token - valid, unexpired, issued for attendance, and matches
                             the authenticated student (g.user_id)
    (b) session_nonce      - session exists+active, scanned nonce matches the
                             current rotating nonce and is unexpired; or the
                             numeric code matches that nonce (wrong/expired
                             -> code_mismatch; after MAX_WRONG_CODE_ATTEMPTS
                             wrong codes -> 429 rate_limit/too_many_attempts).
                             Code-only: no live session shows it ->
                             code_mismatch, two do -> 409 ambiguous_code,
                             and wrong codes are limited per student.
                             A session the conductor ended fails earlier, as
                             {check: "session", error: "session_ended"}
    (c) device_match       - device_id matches the student's registered device
    (d) onchain_lock       - AttendanceLedger.isLocked() is still false

    If everything passes, AttendanceLedger.lockAttendance() is sent from the
    backend signer and the response carries the transaction hash. The
    check-in is then mirrored locally and pushed to the instructor's live
    panel over the session room (Phase 1.2a); the response's
    ``checked_in_at`` is that mirrored check-in time.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("verification_token")
    device_id = body.get("device_id")
    session_id = body.get("session_id")
    nonce = body.get("nonce")
    numeric_code = body.get("numeric_code")
    has_nonce = nonce not in (None, "")
    has_code = numeric_code not in (None, "")
    code_only = has_code and session_id in (None, "")

    required = [("verification_token", token), ("device_id", device_id)]
    if not code_only:
        required.append(("session_id", session_id))
    missing = [field for field, value in required if value in (None, "")]
    if not has_nonce and not has_code:
        missing.append("nonce or numeric_code")
    if missing:
        return api_error(
            "request",
            "missing_fields",
            f"missing required field(s): {', '.join(missing)}",
        )
    if has_nonce and has_code:
        return api_error(
            "request",
            "conflicting_fields",
            "send either nonce (QR) or numeric_code, not both",
        )

    # -- (a) verification token ---------------------------------------------
    try:
        claims = verify(str(token))
    except VerificationTokenError as exc:
        return api_error("verification_token", exc.code, exc.message, 401)

    try:
        student_id = int(claims.get("sub", 0))
    except (TypeError, ValueError):
        return api_error(
            "verification_token", "verification_token_invalid", "token has no valid subject", 401
        )

    if student_id != g.user_id:
        return api_error(
            "verification_token",
            "student_mismatch",
            f"verification token belongs to student {student_id}, not authenticated student {g.user_id}",
            403,
        )

    # -- (b) session + rotating nonce (or the code derived from it) -----------
    if code_only:
        session, error = _session_from_code(student_id, numeric_code)
        if error is not None:
            return error
        session_id = session.id
    else:
        session = (
            db.session.get(AttendanceSession, session_id) if isinstance(session_id, int) else None
        )
    if session is None:
        return api_error(
            "session_nonce", "session_not_found", f"session {session_id} does not exist", 404
        )
    if session.ended_at is not None:
        return api_error(
            "session",
            "session_ended",
            f"session {session_id} was ended by the instructor",
            409,
        )
    if not session.is_active:
        return api_error(
            "session_nonce",
            "session_inactive",
            f"session {session_id} is no longer active",
            409,
        )
    if has_code:
        # A code-only scan was already matched while resolving its session.
        if not code_only:
            if attendance_code.too_many_attempts(student_id, session.id):
                return api_error(
                    "rate_limit",
                    "too_many_attempts",
                    f"too many wrong codes for session {session_id} - scan the QR instead",
                    429,
                )
            # An expired nonce means the code shown with it has expired too.
            if session.nonce_is_expired() or not attendance_code.code_matches(
                session, numeric_code
            ):
                attendance_code.record_wrong_attempt(student_id, session.id)
                return api_error(
                    "session_nonce",
                    "code_mismatch",
                    "the code does not match the session's current code "
                    "(it may have rotated - read the projector again)",
                )
    elif str(nonce) != session.current_nonce:
        return api_error(
            "session_nonce",
            "nonce_mismatch",
            "scanned nonce does not match the session's current nonce "
            "(the QR may have rotated - rescan)",
        )
    elif session.nonce_is_expired():
        return api_error(
            "session_nonce",
            "nonce_expired",
            "the session's current nonce has expired (QR rotation lagged)",
        )

    # -- (c) device binding -----------------------------------------------------
    student = db.session.get(Student, student_id)
    if student is None:
        return api_error(
            "device_match",
            "student_not_found",
            f"student {student_id} (from the verification token) does not exist",
            404,
        )
    if not student.device_id:
        return api_error(
            "device_match",
            "device_not_bound",
            f"student {student_id} has no device registered",
            403,
        )
    if student.device_id != device_id:
        return api_error(
            "device_match",
            "device_mismatch",
            f"device does not match the student's registered device ({student.device_id})",
            403,
        )

    # -- (d) on-chain lock --------------------------------------------------------
    try:
        if blockchain.is_locked(student_id, session_id):
            return api_error(
                "onchain_lock",
                "attendance_already_locked",
                f"attendance for student {student_id} in session {session_id} "
                "is already locked on-chain",
                409,
            )
        result = blockchain.lock_attendance(student_id, session_id)
    except blockchain.BlockchainError as exc:
        status = 409 if exc.code == "attendance_already_locked" else 502
        return api_error("onchain_lock", exc.code, exc.message, status)

    # Mirror the lock locally and push it to the instructor's live panel.
    # Attendance is already anchored on-chain at this point, so neither step
    # may break (or slow down) the response - the emit helper never raises
    # and a failed mirror write is logged and dropped (the response then
    # reports the server's current time as the check-in time).
    checked_in_at = None
    try:
        check_in = live_attendance.record_check_in(
            session.id, student_id, result["transaction_hash"]
        )
        checked_in_at = iso_z(check_in.checked_in_at)
        emit_checked_in(
            session.id,
            live_attendance.check_in_payload(
                check_in, live_attendance.check_in_count(session.id)
            ),
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "live attendance mirror failed for student %s in session %s",
            student_id,
            session_id,
        )

    return jsonify(
        {
            "status": "locked",
            "student_id": student_id,
            "session_id": session_id,
            "transaction_hash": result["transaction_hash"],
            "block_number": result["block_number"],
            "locked_at": result["locked_at"],
            "checked_in_at": checked_in_at or iso_z(utcnow()),
            "signer": result["signer"],
        }
    )
