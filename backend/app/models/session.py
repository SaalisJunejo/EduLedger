"""Attendance session model (Module 1 - Zero-Proxy Attendance Engine).

One row per live class session. The rotating `current_nonce` is the
projector-side secret: the QR code encodes it together with the session id,
and the background job in `app/scheduler.py` replaces it every few seconds so
a screenshot of the QR cannot be replayed later.

`AttendanceCheckIn` is the local mirror of the on-chain locks of a session -
written after AttendanceLedger.lockAttendance() succeeds (chain first, then
the DB, like every other module). It is what lets the live panel show a
running roster without replaying chain events (Phase 1.2a).
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
    # Set only when the conductor explicitly ended the session (Phase 1.2a).
    # Sessions deactivated because a newer one replaced them keep it null, so
    # the two cases stay distinguishable to /attendance/scan.
    ended_at = db.Column(db.DateTime, nullable=True)

    def end(self, now=None) -> None:
        """Close the session for good: no more scans, no more rotation.

        The rotation job and the lazy QR refresh both skip inactive
        sessions, so clearing `is_active` is what stops the nonce.
        """
        self.is_active = False
        self.ended_at = now or utcnow()

    def rotate_nonce(self, now=None, *, ttl_seconds: int) -> None:
        """Replace the nonce and extend its expiry by `ttl_seconds`.

        Callers pass ATTENDANCE_ROTATION_SECONDS; there is no built-in default.
        """
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
            "ended_at": iso_z(self.ended_at) if self.ended_at else None,
        }


class AttendanceCheckIn(db.Model):
    """One student's successful, on-chain-locked check-in to a session."""

    __tablename__ = "attendance_check_ins"

    id = db.Column(db.Integer, primary_key=True)

    # ON DELETE CASCADE so deleting a session (what the test scripts do when
    # they clean up after themselves) takes its mirrored check-ins with it.
    session_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "attendance_sessions.id",
            name="fk_attendance_check_ins_session",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Student.id == User.id by convention (no FK), like everywhere else.
    student_id = db.Column(db.Integer, nullable=False, index=True)

    checked_in_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Hash of the AttendanceLedger.lockAttendance() transaction that is the
    # real record; this row is only the fast local view of it.
    onchain_tx_hash = db.Column(db.String(66), nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "session_id", "student_id", name="uq_attendance_check_ins_session_student"
        ),
    )

    def to_dict(self) -> dict:
        """JSON-friendly view of the check-in."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "student_id": self.student_id,
            "checked_in_at": iso_z(self.checked_in_at),
            "onchain_tx_hash": self.onchain_tx_hash,
        }
