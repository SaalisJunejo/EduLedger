"""Faculty workload models (Module 3 - Faculty Workload Ledger).

Four tables back the module:

- ``ScheduledClass``  - one timetable slot (professor, room, course, start).
- ``WorkloadSession`` - the *live* class meeting: who is conducting it right
  now (the professor, or a substitute that redeemed a token) and which
  Module 1 ``AttendanceSession`` row carries its rotating QR nonce. The QR
  machinery itself is deliberately NOT duplicated here - faculty sessions
  are plain ``AttendanceSession`` rows (class_id ``workload-<id>``), so the
  existing background nonce rotation and the existing
  GET /api/v1/session/<id>/qr projector endpoint serve them unchanged.
- ``SessionLog``      - the permanent record of a class's final outcome,
  created when the outcome is anchored on-chain (student scan -> CONDUCTED,
  zero-attendance declaration -> CONDUCTED_ZERO_STUDENTS, background
  auto-flag -> UNCONDUCTED). `onchain_tx_hash` ties the row to its
  WorkloadLedger transaction.
- ``SubstituteToken`` - a short-lived signed authorization (JWT) letting a
  named substitute cover one specific class; redeemed once, in exchange for
  the right to start that class's session.

Status names mirror the WorkloadLedger.sol `WorkloadStatus` enum verbatim
(the same convention Proposal uses with the RecordAuditTrail enum), so
local rows compare equal with on-chain state. `to_dict()` emits camelCase,
like every other model.
"""

from ..extensions import db
from ..util import iso_z, utcnow

# SessionLog outcome states - the names mirror the contract's WorkloadStatus
# enum members verbatim, so local rows compare equal with on-chain state.
STATUS_CONDUCTED = "CONDUCTED"
STATUS_CONDUCTED_ZERO_STUDENTS = "CONDUCTED_ZERO_STUDENTS"
STATUS_UNCONDUCTED = "UNCONDUCTED"


class ScheduledClass(db.Model):
    """One timetable slot that a professor is scheduled to teach."""

    __tablename__ = "scheduled_classes"

    id = db.Column(db.Integer, primary_key=True)

    # Professor the class is scheduled for (opaque id - there is no
    # professor table yet, mirroring how Module 2 treats student ids).
    professor_id = db.Column(db.Integer, nullable=False, index=True)

    room = db.Column(db.String(64), nullable=False)

    start_time = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Course code, e.g. "BSCS-401" (descriptive; not a courses-table FK).
    course_id = db.Column(db.String(64), nullable=False)

    def to_dict(self) -> dict:
        """JSON-friendly view of the scheduled class."""
        return {
            "id": self.id,
            "professorId": self.professor_id,
            "room": self.room,
            "startTime": iso_z(self.start_time),
            "courseId": self.course_id,
        }


class WorkloadSession(db.Model):
    """The live meeting of a scheduled class, and who is conducting it.

    Rows are created by POST /api/v1/faculty/session/start (conductor =
    the class's professor) and by /api/v1/faculty/substitute/redeem
    (conductor = the substitute). Only one row per class is active at a
    time; starting a new session deactivates the previous one, exactly like
    the Module 1 attendance session lifecycle.
    """

    __tablename__ = "workload_sessions"

    id = db.Column(db.Integer, primary_key=True)

    class_id = db.Column(
        db.Integer, db.ForeignKey("scheduled_classes.id"), nullable=False, index=True
    )

    # The Module 1 AttendanceSession carrying the rotating QR nonce.
    attendance_session_id = db.Column(
        db.Integer, db.ForeignKey("attendance_sessions.id"), nullable=False, index=True
    )

    # Professor (or substitute) currently conducting the class - the person
    # a later SessionLog credits the workload to.
    conductor_id = db.Column(db.Integer, nullable=False)

    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the live session."""
        return {
            "id": self.id,
            "classId": self.class_id,
            "attendanceSessionId": self.attendance_session_id,
            "conductorId": self.conductor_id,
            "startedAt": iso_z(self.started_at),
            "isActive": self.is_active,
        }


class SessionLog(db.Model):
    """The final, on-chain-anchored outcome of a scheduled class."""

    __tablename__ = "session_logs"

    id = db.Column(db.Integer, primary_key=True)

    class_id = db.Column(
        db.Integer, db.ForeignKey("scheduled_classes.id"), nullable=False, index=True
    )

    # STATUS_CONDUCTED / STATUS_CONDUCTED_ZERO_STUDENTS / STATUS_UNCONDUCTED.
    status = db.Column(db.String(32), nullable=False)

    # Professor (or substitute) the workload is credited to - the WorkloadSession
    # conductor at the time the outcome was recorded.
    conducted_by = db.Column(db.Integer, nullable=False)

    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Hash of the WorkloadLedger.logWorkload() transaction that anchored
    # this entry (the chain is the source of truth; this is the pointer).
    onchain_tx_hash = db.Column(db.String(66), nullable=False)

    scheduled_class = db.relationship("ScheduledClass", backref="session_logs")

    def to_dict(self) -> dict:
        """JSON-friendly view of the log entry."""
        return {
            "id": self.id,
            "classId": self.class_id,
            "status": self.status,
            "conductedBy": self.conducted_by,
            "timestamp": iso_z(self.timestamp),
            "onchainTxHash": self.onchain_tx_hash,
        }


class SubstituteToken(db.Model):
    """A short-lived signed authorization for a substitute to cover a class."""

    __tablename__ = "substitute_tokens"

    id = db.Column(db.Integer, primary_key=True)

    # Professor who issued the token (the class's scheduled professor).
    issued_by = db.Column(db.Integer, nullable=False, index=True)

    # Substitute who redeemed the token (null until redemption).
    redeemed_by = db.Column(db.Integer, nullable=True)

    class_id = db.Column(
        db.Integer, db.ForeignKey("scheduled_classes.id"), nullable=False, index=True
    )

    # The JWT string itself (unique - redemption looks the row up by it).
    token = db.Column(db.String(512), nullable=False, unique=True)

    expires_at = db.Column(db.DateTime, nullable=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        """JSON-friendly view - never echoes the raw JWT back."""
        return {
            "id": self.id,
            "issuedBy": self.issued_by,
            "redeemedBy": self.redeemed_by,
            "classId": self.class_id,
            "expiresAt": iso_z(self.expires_at),
            "redeemedAt": iso_z(self.redeemed_at) if self.redeemed_at else None,
        }
