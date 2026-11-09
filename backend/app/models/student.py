"""Student model (Module 1 - Zero-Proxy Attendance Engine).

A student's identity is anchored to one registered device (`device_id`,
bound via POST /api/v1/student/register-device) plus the device-bound HMAC
secret issued by POST /api/v1/students/device/enroll (`device_secret`,
Phase 1.7a / decision D4) and optional biometric embeddings: the face
embedding backs POST /api/v1/attendance/verify-identity,
and the periocular (eye-region) embedding is reserved for the PRD's
anti-spoofing future scope.

Embeddings are stored as JSON dicts - ``{"provider": str, "dims": int,
"vector": [floats], "enrolled_at": iso}`` - produced by
`app/services/face_embedding.py`; never expose the raw vectors via the API.
"""

from ..extensions import db


class Student(db.Model):
    """A student account with device binding and biometric embeddings."""

    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    # Bound via POST /api/v1/student/register-device; unique across students
    # (one student identity per device). Null until first binding.
    device_id = db.Column(db.String(255), nullable=True, unique=True, index=True)

    # Device-bound HMAC secret (Phase 1.7a, decision D4): base64url of 32
    # random bytes, issued ONCE by POST /api/v1/students/device/enroll and
    # held by the phone's OS behind a biometric prompt. The server verifies a
    # challenge-response with it (POST /students/verify-identity-device) and
    # clears it on an admin rebind. Stored in plain form - FYP simplification.
    device_secret = db.Column(db.String(64), nullable=True)

    # Face embedding dict (see module docstring); null until enrolled.
    face_embedding = db.Column(db.JSON, nullable=True)

    # Reserved: periocular (eye-region) embedding for liveness/anti-spoofing.
    periocular_embedding = db.Column(db.JSON, nullable=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the student - never the raw embeddings."""
        return {
            "id": self.id,
            "name": self.name,
            "device_id": self.device_id,
            "has_device_secret": bool(self.device_secret),
            "has_face_embedding": bool(self.face_embedding),
            "has_periocular_embedding": bool(self.periocular_embedding),
        }
