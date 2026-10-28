"""Identity verification + attendance scan endpoints (Module 1).

- POST /api/v1/attendance/verify-identity - face match (or WebAuthn stub) -> 30s JWT
- POST /api/v1/attendance/scan            - the full check-in: token, nonce
  (or the 6-digit numeric code), device, on-chain lock. Checks run in a
  fixed order (a) to (d) and every failure response names the check that
  failed, for demo debugging.
"""

from datetime import datetime, timezone

from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import AttendanceSession, Student
from ..services import attendance_code, blockchain
from ..services.auth import require_auth
from ..services.face_embedding import (
    EmbeddingMismatchError,
    FaceImageError,
    compare_embeddings,
    embed_face_image,
)
from ..services.verification_token import VerificationTokenError, mint, verify
from ..services.webauthn import WebAuthnError, verify_assertion
from .errors import api_error
from . import api_v1


def _iso_from_unix(timestamp: float) -> str:
    """Format a unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


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
    ``nonce``.

    Validation order (each failure response names its check):
    (a) verification_token - valid, unexpired, issued for attendance, and matches
                             the authenticated student (g.user_id)
    (b) session_nonce      - session exists+active, scanned nonce matches the
                             current rotating nonce and is unexpired; or the
                             numeric code matches that nonce (wrong/expired
                             -> code_mismatch; after MAX_WRONG_CODE_ATTEMPTS
                             wrong codes -> 429 rate_limit/too_many_attempts)
    (c) device_match       - device_id matches the student's registered device
    (d) onchain_lock       - AttendanceLedger.isLocked() is still false

    If everything passes, AttendanceLedger.lockAttendance() is sent from the
    backend signer and the response carries the transaction hash.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("verification_token")
    device_id = body.get("device_id")
    session_id = body.get("session_id")
    nonce = body.get("nonce")
    numeric_code = body.get("numeric_code")
    has_nonce = nonce not in (None, "")
    has_code = numeric_code not in (None, "")

    missing = [
        field
        for field, value in (
            ("verification_token", token),
            ("device_id", device_id),
            ("session_id", session_id),
        )
        if value in (None, "")
    ]
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
    session = db.session.get(AttendanceSession, session_id) if isinstance(session_id, int) else None
    if session is None:
        return api_error(
            "session_nonce", "session_not_found", f"session {session_id} does not exist", 404
        )
    if not session.is_active:
        return api_error(
            "session_nonce",
            "session_inactive",
            f"session {session_id} is no longer active",
            409,
        )
    if has_code:
        if attendance_code.too_many_attempts(student_id, session.id):
            return api_error(
                "rate_limit",
                "too_many_attempts",
                f"too many wrong codes for session {session_id} - scan the QR instead",
                429,
            )
        # An expired nonce means the code shown with it has expired too.
        if session.nonce_is_expired() or not attendance_code.code_matches(session, numeric_code):
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

    return jsonify(
        {
            "status": "locked",
            "student_id": student_id,
            "session_id": session_id,
            "transaction_hash": result["transaction_hash"],
            "block_number": result["block_number"],
            "locked_at": result["locked_at"],
            "signer": result["signer"],
        }
    )
