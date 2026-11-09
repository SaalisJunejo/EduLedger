"""Student identity endpoints (Module 1 - Zero-Proxy Attendance Engine).

- POST /api/v1/student/enroll-face              - demo enrollment: stores the face embedding
- POST /api/v1/student/register-device          - binds the student's device (first binding only)

Device-bound biometric credential (Phase 1.7a, decision D4) - the Expo app
only reports "the OS biometric prompt passed on this device", so the server
verifies a challenge-response with a secret the OS releases after that
prompt (see app/services/device_credential.py):

- POST /api/v1/students/device/enroll           - issues the device secret ONCE
- GET  /api/v1/students/identity/challenge      - single-use challenge, short TTL
- POST /api/v1/students/verify-identity-device  - HMAC check -> the same 30 s
  verification token POST /attendance/verify-identity mints for a face match,
  with method "device-biometric". /attendance/scan accepts either token.

Device rebinding (lost/changed phone) is an admin operation:
POST /api/v1/admin/rebind-device (see app/api/admin.py) - it also clears the
device secret, so the new phone must enroll again.
"""

from datetime import datetime, timezone

from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import Student, User
from ..services import device_credential
from ..services.auth import require_auth
from ..services.face_embedding import FaceImageError, embed_face_image
from ..services.verification_token import mint
from .errors import api_error
from . import api_v1

# Demo-friendly messages for the two challenge failures (the codes come from
# device_credential.consume_challenge).
_CHALLENGE_MESSAGES = {
    device_credential.CHALLENGE_EXPIRED: (
        "the challenge is unknown or expired - request a new one from "
        "GET /api/v1/students/identity/challenge"
    ),
    device_credential.CHALLENGE_USED: (
        "this challenge was already used - every challenge is single-use, "
        "request a new one"
    ),
}


def _iso_from_unix(timestamp: float) -> str:
    """Format a unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def ensure_student_row(user_id: int) -> Student:
    """Return the Student row with ``id == user_id``, creating it if missing.

    POST /auth/register/student creates a User and a StudentProfile but no
    Student row, and ``Student.id == User.id`` by convention, so the device
    routes would otherwise answer 404 ``student_not_found`` for a freshly
    registered account. The new row takes the user's full name; every other
    column is nullable. Idempotent: an existing row is returned unchanged.
    """
    student = db.session.get(Student, user_id)
    if student is not None:
        return student

    user = db.session.get(User, user_id)
    name = user.full_name if user and user.full_name else f"Student {user_id}"
    # users.full_name is 128 chars, students.name is 120.
    student = Student(id=user_id, name=name[:120])
    db.session.add(student)
    db.session.commit()
    return student


@api_v1.post("/student/enroll-face")
@require_auth(roles=["STUDENT"])
def enroll_face():
    """Demo enrollment: create (or update) a student and store a face embedding.

    Body: ``{"face_image": "<base64 image>"}`` (optional: ``name``).
    Identity is derived from the authenticated student token (g.user_id).
    Any student_id in the body is ignored.
    """
    body = request.get_json(silent=True) or {}
    student_id = g.user_id
    name = str(body.get("name") or "").strip()
    face_image = body.get("face_image")

    if not face_image:
        return api_error(
            "enrollment",
            "missing_fields",
            "face_image (base64) is required",
        )

    try:
        embedding = embed_face_image(str(face_image))
    except FaceImageError as exc:
        return api_error("enrollment", "invalid_face_image", str(exc))

    student = db.session.get(Student, student_id)
    created = student is None
    if created:
        if not name:
            from ..models import User
            user = db.session.get(User, student_id)
            name = user.full_name if user and user.full_name else f"Student {student_id}"
        student = Student(id=student_id, name=name)
    elif name:
        student.name = name

    student.face_embedding = embedding
    db.session.add(student)
    db.session.commit()

    return (
        jsonify(
            {
                "student": student.to_dict(),
                "embedding": {"provider": embedding["provider"], "dims": embedding["dims"]},
                "created": created,
            }
        ),
        201,
    )


@api_v1.post("/student/register-device")
@require_auth(roles=["STUDENT"])
def register_device():
    """Bind a client-generated deviceId to an authenticated student account.

    Body: ``{"device_id": "<keychain/keystore uuid>"}``.
    Identity is derived from the authenticated student token (g.user_id).
    The device id is generated and stored on the client (iOS Keychain /
    Android Keystore). Only the FIRST binding is allowed here: if a different
    device is already bound the request is rejected with `device_already_bound`
    and an administrator must rebind via POST /api/v1/admin/rebind-device.
    Binding the already-bound device again is idempotent.
    """
    body = request.get_json(silent=True) or {}
    student_id = g.user_id
    device_id = str(body.get("device_id") or "").strip()

    if not device_id:
        return api_error(
            "device_binding", "missing_fields", "device_id is required"
        )

    student = ensure_student_row(student_id)

    if student.device_id is None:
        # A device can belong to only one student.
        taken_by = Student.query.filter(
            Student.device_id == device_id, Student.id != student.id
        ).first()
        if taken_by is not None:
            return api_error(
                "device_binding",
                "device_taken",
                f"device {device_id} is already bound to student {taken_by.id}",
                409,
            )
        student.device_id = device_id
        db.session.commit()
        return jsonify({"student": student.to_dict(), "already_bound": False})

    if student.device_id == device_id:
        return jsonify({"student": student.to_dict(), "already_bound": True})

    return api_error(
        "device_binding",
        "device_already_bound",
        f"student {student_id} already has device {student.device_id} bound - "
        "an administrator must rebind via POST /api/v1/admin/rebind-device",
        409,
        current_device_id=student.device_id,
    )


@api_v1.post("/students/device/enroll")
@require_auth(roles=["STUDENT"])
def enroll_device_secret():
    """Issue the device-bound HMAC secret for this student's phone (D4).

    Body: ``{"device_id": "<keychain/keystore uuid>"}``.
    Identity is derived from the authenticated student token (g.user_id).

    The device must be the student's bound device; if nothing is bound yet
    this binds it first, exactly like POST /student/register-device (same
    `device_taken` / `device_already_bound` rules - a student still cannot
    move their binding to another phone themselves).

    The 32-byte secret is returned ONCE, in this response only: the app must
    store it in the Keychain / Keystore behind biometric access control. A
    student who already has a secret gets 409 `secret_already_enrolled` - an
    administrator rebinds the device (which clears the secret) first.
    """
    body = request.get_json(silent=True) or {}
    student_id = g.user_id
    device_id = str(body.get("device_id") or "").strip()

    if not device_id:
        return api_error("device_enrollment", "missing_fields", "device_id is required")

    student = ensure_student_row(student_id)

    if student.device_id is None:
        # A device can belong to only one student (same rule as register-device).
        taken_by = Student.query.filter(
            Student.device_id == device_id, Student.id != student.id
        ).first()
        if taken_by is not None:
            return api_error(
                "device_enrollment",
                "device_taken",
                f"device {device_id} is already bound to student {taken_by.id}",
                409,
            )
        student.device_id = device_id
    elif student.device_id != device_id:
        return api_error(
            "device_enrollment",
            "device_already_bound",
            f"student {student_id} has device {student.device_id} bound - enroll from "
            "that device, or ask an administrator to rebind via "
            "POST /api/v1/admin/rebind-device",
            409,
            current_device_id=student.device_id,
        )

    if student.device_secret:
        return api_error(
            "device_enrollment",
            "secret_already_enrolled",
            f"student {student_id} already has a device secret - it is returned only "
            "once; an administrator must rebind the device to issue a new one",
            409,
        )

    secret = device_credential.generate_secret()
    student.device_secret = secret
    db.session.add(student)
    db.session.commit()

    return (
        jsonify(
            {
                "student": student.to_dict(),
                "device_id": student.device_id,
                # Returned ONCE - the server never exposes it again.
                "device_secret": secret,
                "algorithm": "HMAC-SHA256",
                "response_format": 'hex(HMAC_SHA256(secret, challenge + ":" + device_id))',
                "note": "store this secret behind the OS biometric prompt - "
                "it is shown only in this response",
            }
        ),
        201,
    )


@api_v1.get("/students/identity/challenge")
@require_auth(roles=["STUDENT"])
def identity_challenge():
    """A single-use challenge for the device-biometric check (D4).

    Returns ``{"challenge": "<base64url>", "expires_in": 30}``. The challenge
    is stored per student with a TTL (Redis when reachable, otherwise this
    process's memory) and is consumed by the first
    POST /students/verify-identity-device that presents it.
    """
    ensure_student_row(g.user_id)
    challenge, ttl = device_credential.issue_challenge(g.user_id)
    return jsonify({"challenge": challenge, "expires_in": ttl})


@api_v1.post("/students/verify-identity-device")
@require_auth(roles=["STUDENT"])
def verify_identity_device():
    """Verify the device-bound biometric response -> 30 s verification token.

    Body: ``{"device_id": "...", "challenge": "...", "response": "<hex>"}``
    where ``response`` is ``HMAC_SHA256(secret, challenge + ":" + device_id)``,
    computed by the app with the secret its OS released after a successful
    biometric prompt. Identity comes from the student token (g.user_id).

    Checks, in order (all reported as ``check: "identity"``):
    - `not_enrolled`       - no device secret issued yet (a Student row is
                             created on demand, see ensure_student_row)
    - `device_mismatch`    - device_id is not the student's bound device
    - `challenge_expired`  - unknown or older than its TTL
    - `challenge_used`     - already spent (single use)
    - `bad_response`       - the HMAC does not match (the challenge is spent
                             either way, so a guess cannot be retried)

    On success the response matches the face path's shape exactly - the same
    claims from the same minter, only `method` differs - so
    POST /attendance/scan accepts it unchanged.
    """
    body = request.get_json(silent=True) or {}
    student_id = g.user_id
    device_id = str(body.get("device_id") or "").strip()
    challenge = str(body.get("challenge") or "").strip()
    response = body.get("response")

    missing = [
        field
        for field, value in (
            ("device_id", device_id),
            ("challenge", challenge),
            ("response", response),
        )
        if not value
    ]
    if missing:
        return api_error(
            "identity",
            "missing_fields",
            f"missing required field(s): {', '.join(missing)}",
        )

    student = ensure_student_row(student_id)
    if not student.device_secret:
        return api_error(
            "identity",
            "not_enrolled",
            f"student {student_id} has no device secret - enroll via "
            "POST /api/v1/students/device/enroll",
            409,
        )

    if student.device_id != device_id:
        return api_error(
            "identity",
            "device_mismatch",
            f"device does not match the student's registered device ({student.device_id})",
            403,
        )

    # Spend the challenge before comparing, so a wrong response cannot be
    # retried against the same challenge.
    outcome = device_credential.consume_challenge(student_id, challenge)
    if outcome != device_credential.CHALLENGE_OK:
        return api_error("identity", outcome, _CHALLENGE_MESSAGES[outcome], 401)

    if not device_credential.response_matches(
        student.device_secret, challenge, device_id, response
    ):
        return api_error(
            "identity",
            "bad_response",
            "the challenge response does not match the enrolled device secret",
            401,
        )

    token, expires_at = mint(student.id, "device-biometric", device_id=device_id)
    return jsonify(
        {
            "verification_token": token,
            "method": "device-biometric",
            "expires_at": _iso_from_unix(expires_at),
            "expires_in": current_app.config["VERIFICATION_TOKEN_SECONDS"],
        }
    )
