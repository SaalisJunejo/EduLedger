"""Live attendance roster: who checked in, and who may watch (Phase 1.2a).

Shared by three callers so none of them duplicates the rules:

- `app/api/attendance.py`  - mirrors a successful on-chain lock locally and
  builds the payload for the "student_checked_in" event.
- `app/sockets.py`         - authorizes `join_session` and reports the
  current count when a watcher joins.
- `app/api/sessions.py`    - GET /session/<id>/attendees (page load and
  reconnect) and POST /session/<id>/end.

`conductor_id()` is the single definition of "whose session is this": plain
attendance sessions belong to the professor of the ScheduledClass whose id
they carry, while faculty workload sessions (class_id ``workload-<id>``)
belong to the WorkloadSession conductor - which is the substitute once a
substitute token has been redeemed (doc 7.3).
"""

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    AttendanceCheckIn,
    AttendanceSession,
    ScheduledClass,
    StudentProfile,
    User,
    WorkloadSession,
)
from ..util import iso_z

# app/api/faculty.py namespaces its sessions with this prefix.
WORKLOAD_CLASS_PREFIX = "workload-"


def conductor_id(session: AttendanceSession) -> int | None:
    """The user id of the faculty member conducting `session`, if known."""
    class_id = (session.class_id or "").strip()

    if class_id.startswith(WORKLOAD_CLASS_PREFIX):
        # The WorkloadSession row is authoritative: it names the substitute
        # when one redeemed a token, and the professor otherwise.
        workload_session = (
            WorkloadSession.query.filter_by(attendance_session_id=session.id)
            .order_by(WorkloadSession.id.desc())
            .first()
        )
        if workload_session is not None:
            return workload_session.conductor_id
        class_id = class_id[len(WORKLOAD_CLASS_PREFIX) :]

    if not class_id.isdigit():
        return None
    scheduled_class = db.session.get(ScheduledClass, int(class_id))
    return scheduled_class.professor_id if scheduled_class is not None else None


def record_check_in(session_id: int, student_id: int, onchain_tx_hash: str | None = None):
    """Mirror one on-chain lock locally; idempotent per (session, student)."""
    existing = AttendanceCheckIn.query.filter_by(
        session_id=session_id, student_id=student_id
    ).first()
    if existing is not None:
        return existing

    check_in = AttendanceCheckIn(
        session_id=session_id,
        student_id=student_id,
        onchain_tx_hash=onchain_tx_hash,
    )
    db.session.add(check_in)
    try:
        db.session.commit()
    except IntegrityError:
        # Two concurrent scans of the same student: keep the row that won.
        db.session.rollback()
        return AttendanceCheckIn.query.filter_by(
            session_id=session_id, student_id=student_id
        ).first()
    return check_in


def check_in_count(session_id: int) -> int:
    """How many students have checked in to this session so far."""
    return AttendanceCheckIn.query.filter_by(session_id=session_id).count()


def check_in_payload(check_in: AttendanceCheckIn, count: int) -> dict:
    """The shape the live panel consumes, for one check-in.

    Name and roll number come from the User / StudentProfile rows; a student
    whose profile is missing is shown as "Student <id>" rather than blank.
    """
    user = db.session.get(User, check_in.student_id)
    profile = StudentProfile.query.filter_by(user_id=check_in.student_id).first()
    fallback = f"Student {check_in.student_id}"
    return {
        "student_id": check_in.student_id,
        "full_name": (user.full_name if user is not None and user.full_name else fallback),
        "roll_number": (
            profile.roll_number if profile is not None and profile.roll_number else fallback
        ),
        "checked_in_at": iso_z(check_in.checked_in_at),
        "count": count,
    }


def attendees(session_id: int) -> list[dict]:
    """Every check-in of a session, oldest first.

    Each entry carries the same fields as the "student_checked_in" event, so
    a page load and the live stream render through the same code path;
    `count` is the running total at that point in the session.
    """
    rows = (
        AttendanceCheckIn.query.filter_by(session_id=session_id)
        .order_by(AttendanceCheckIn.checked_in_at.asc(), AttendanceCheckIn.id.asc())
        .all()
    )
    return [check_in_payload(row, index + 1) for index, row in enumerate(rows)]
