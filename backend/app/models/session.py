"""Attendance session model (Module 1 - Zero-Proxy Attendance Engine).

One row per live class session. The rotating `current_nonce` is the
projector-side secret: the QR code encodes it together with the session id,
and the background job in `app/scheduler.py` replaces it every few seconds so
a screenshot of the QR cannot be replayed later.
"""

import secrets
import time
from datetime import timedelta

from ..extensions import db
from ..util import iso_z, utcnow


def generate_nonce(now: float | None = None) -> str:
    """Build a fresh unpredictable nonce: hex timestamp + URL-safe random part.

    The timestamp prefix makes each nonce traceable to its rotation window
    (useful for debugging); the 24-byte random suffix is what makes it
    unguessable.
    """
    timestamp = int(now if now is not None else time.time())
    return f"{timestamp:x}.{secrets.token_urlsafe(24)}"


class AttendanceSession(db.Model):
    """A live attendance session for one class, driven by a rotating nonce."""

    __tablename__ = "attendance_sessions"

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.String(64), nullable=False, index=True)
    start_time = db.Column(db.DateTime, nullable=False, default=utcnow)
    current_nonce = db.Column(db.String(128), nullable=False)
    nonce_expires_at = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def rotate_nonce(self, now=None, ttl_seconds: int = 5) -> None:
        """Replace the nonce and extend its expiry by `ttl_seconds`."""
        now = now or utcnow()
        self.current_nonce = generate_nonce(now.timestamp())
        self.nonce_expires_at = now + timedelta(seconds=ttl_seconds)

    def nonce_is_expired(self, now=None) -> bool:
        """True once the nonce has outlived its rotation window."""
        return self.nonce_expires_at <= (now or utcnow())

    def to_dict(self) -> dict:
        """JSON-friendly view of the session."""
        return {
            "id": self.id,
            "class_id": self.class_id,
            "start_time": iso_z(self.start_time),
            "current_nonce": self.current_nonce,
            "nonce_expires_at": iso_z(self.nonce_expires_at),
            "is_active": self.is_active,
        }
