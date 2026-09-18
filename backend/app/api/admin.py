"""Admin endpoints (Module 1).

- POST /api/v1/admin/rebind-device - force-bind a new device to a student

STUB: no authentication yet. When the JWT + role layer lands (roadmap), this
blueprint gets an admin-role decorator; until then it is local-demo only.
"""

from flask import jsonify, request

from ..extensions import db
from ..models import Student
from .errors import api_error
from . import api_v1


@api_v1.post("/admin/rebind-device")
def rebind_device():
    """Force-bind a device to a student (lost/replaced phone, admin override).

    Body: ``{"student_id": 2024001, "device_id": "<new device id>"}``.
    Replaces any existing binding - this is the only path that may do so
    (students cannot rebind themselves via /student/register-device).
    """
    body = request.get_json(silent=True) or {}
    student_id = body.get("student_id")
    device_id = str(body.get("device_id") or "").strip()

    if not isinstance(student_id, int) or not device_id:
        return api_error(
            "admin_rebind", "missing_fields", "student_id (int) and device_id are required"
        )

    student = db.session.get(Student, student_id)
    if student is None:
        return api_error(
            "admin_rebind", "student_not_found", f"student {student_id} does not exist", 404
        )

    taken_by = Student.query.filter(
        Student.device_id == device_id, Student.id != student.id
    ).first()
    if taken_by is not None:
        return api_error(
            "admin_rebind",
            "device_taken",
            f"device {device_id} is already bound to student {taken_by.id}",
            409,
        )

    previous_device_id = student.device_id
    student.device_id = device_id
    db.session.commit()

    return jsonify(
        {
            "student": student.to_dict(),
            "previous_device_id": previous_device_id,
            "note": "admin rebind stub - authentication not enforced yet",
        }
    )
