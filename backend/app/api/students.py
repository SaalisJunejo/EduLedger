"""Student identity endpoints (Module 1 - Zero-Proxy Attendance Engine).

- POST /api/v1/student/enroll-face     - demo enrollment: stores the face embedding
- POST /api/v1/student/register-device - binds the student's device (first binding only)

Device rebinding (lost/changed phone) is an admin operation:
POST /api/v1/admin/rebind-device (see app/api/admin.py).
"""

from flask import g, jsonify, request

from ..extensions import db
from ..models import Student
from ..services.auth import require_auth
from ..services.face_embedding import FaceImageError, embed_face_image
from .errors import api_error
from . import api_v1


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

    student = db.session.get(Student, student_id)
    if student is None:
        return api_error(
            "device_binding", "student_not_found", f"student {student_id} does not exist", 404
        )

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
