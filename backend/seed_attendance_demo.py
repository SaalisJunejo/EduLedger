r"""Development-only Phase 2.9 attendance sample.

Default mode is LOCAL ONLY: no chain call, no SessionLog, no IPFS. Run from
backend/ after applying the Phase 2.9 SQL:

    .\venv\Scripts\python.exe seed_attendance_demo.py

The seed owns its demo faculty (attendance.demo.faculty@example.edu) and three
demo students, inside an isolated "Attendance Demo" department. It never
reuses, reassigns or closes anything real. Reruns leave a complete seed intact;
an incomplete seed requires reconciliation, not blind retries.

Outcomes come from the read-only fallback in app/services/attendance_portal.py
(no SessionLog is written by default), which yields CONDUCTED on three days,
CONDUCTED_ZERO on the fourth started class, UNCONDUCTED on the class with no
session, and HOLIDAY on the closed day.

Optional chain mode (--allow-chain-writes) additionally writes the two
existing ledgers: AttendanceLedger.lockAttendance per capture and
WorkloadLedger.logWorkload per class plus five SessionLog rows. Those entries
are append-only and cannot be deleted, so a partial run must be reconciled by
hand; the same classId/sessionId can never be logged twice.
"""

import argparse
import os
import sys
from datetime import timedelta

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    AttendanceCheckIn, AttendanceSession, CalendarClosure, Course, FacultyProfile,
    Period, Room, ScheduledClass, Section, SessionLog, StudentProfile,
    StudentSection, Term, TermOffering, TimetableSlot, User, UserRole,
)
from app.services import blockchain, occurrences, workload  # noqa: E402
from app.services.auth import hash_password  # noqa: E402

DEPARTMENT = "Attendance Demo"
TERM_NAME = "Attendance Demo (Phase 2.9)"
FACULTY_EMAIL = "attendance.demo.faculty@example.edu"
FACULTY_EMPLOYEE_ID = "ATT-DEMO-FACULTY"
DEFAULT_PASSWORD = "AttendanceDemo123!"
# (offset from the Monday anchor, status the report must derive)
DAYS = ((0, "CONDUCTED"), (1, "CONDUCTED"), (2, "CONDUCTED"),
        (3, "CONDUCTED_ZERO"), (4, "UNCONDUCTED"), (7, "HOLIDAY"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                        help="Password for the demo faculty and student accounts (min 12 chars)")
    parser.add_argument("--allow-chain-writes", action="store_true",
                        help="Also write the append-only attendance/workload ledgers (irreversible)")
    args = parser.parse_args()
    if os.getenv("FLASK_CONFIG", "development") != "development":
        print("Refused: FLASK_CONFIG must be development.")
        return 1
    if len(args.password) < 12:
        print("Refused: use a demo password of at least 12 characters.")
        return 1
    if args.allow_chain_writes and not os.getenv("HARDHAT_RPC_URL"):
        print("Refused: chain mode needs HARDHAT_RPC_URL and both contract addresses.")
        return 1

    app = create_app("development")
    with app.app_context():
        try:
            return seed(args)
        except Exception as exc:
            db.session.rollback()
            print(f"FAIL: {type(exc).__name__}: {exc}")
            if args.allow_chain_writes:
                print("Chain mode: already committed ledger entries cannot be undone; reconcile first.")
            return 1


def seed(args) -> int:
    existing = Term.query.filter_by(name=TERM_NAME).first()
    if existing is not None:
        return report_existing(existing, args)
    if User.query.filter_by(email=FACULTY_EMAIL).first() is not None:
        raise ValueError(f"{FACULTY_EMAIL} exists without the demo term; refusing to adopt it")
    if Section.query.filter_by(department=DEPARTMENT).first() is not None:
        raise ValueError("Attendance Demo department already exists without this seed; left untouched")
    if Course.query.filter_by(code="ATT-DEMO").first() or Room.query.filter_by(name="Attendance Demo Room").first():
        raise ValueError("demo course/room names already exist; left untouched")

    today = occurrences.today()
    anchor = today - timedelta(days=today.weekday() + 28)  # a Monday, four weeks back
    term = Term(name=TERM_NAME, start_date=anchor, end_date=anchor + timedelta(days=11), is_active=False)
    section = Section(programme="Attendance Demo", batch_year=anchor.year, letter="A", department=DEPARTMENT)
    course = Course(code="ATT-DEMO", title="Attendance Demonstration", department=DEPARTMENT, is_lab=False)
    room = Room(name="Attendance Demo Room", type="LECTURE", department=DEPARTMENT, capacity=30)
    db.session.add_all([term, section, course, room])
    db.session.flush()

    faculty = User(role=UserRole.FACULTY, email=FACULTY_EMAIL, password_hash=hash_password(args.password),
                   full_name="Attendance Demo Faculty", department=DEPARTMENT, is_active=True)
    db.session.add(faculty)
    db.session.flush()
    db.session.add(FacultyProfile(user_id=faculty.id, employee_id=FACULTY_EMPLOYEE_ID))

    offering = TermOffering(term_id=term.id, section_id=section.id)
    db.session.add(offering)
    db.session.flush()

    students = []
    for index in range(3):
        email = f"attendance.demo.{index + 1}@example.edu"
        if User.query.filter_by(email=email).first() is not None:
            raise ValueError(f"{email} already exists; never reassigning its section")
        user = User(role=UserRole.STUDENT, email=email, password_hash=hash_password(args.password),
                    full_name=f"Attendance Demo Student {index + 1}", department=DEPARTMENT)
        db.session.add(user)
        db.session.flush()
        students.append(user.id)
        db.session.add(StudentProfile(user_id=user.id, roll_number=f"ATT-DEMO-{index + 1:03d}",
                                      batch=str(anchor.year), programme=section.programme))
        db.session.add(StudentSection(user_id=user.id, section_id=section.id))

    period = Period.query.filter_by(ordinal=1).one()
    slots = {}
    for weekday in range(5):
        slot = TimetableSlot(offering_id=offering.id, course_id=course.id, faculty_id=faculty.id,
                             room_id=room.id, day_of_week=weekday, period_id=period.id)
        db.session.add(slot)
        db.session.flush()
        slots[weekday] = slot

    rows, sessions = [], []
    for offset, _expected in DAYS:
        day = anchor + timedelta(days=offset)
        start, end = occurrences.period_times(day, period)
        row = ScheduledClass(slot_id=slots[day.weekday()].id, term_id=term.id,
                             professor_id=faculty.id, course_code=course.code, room=room.name,
                             day_of_week=day.weekday(), start_time=start, end_time=end)
        db.session.add(row)
        db.session.flush()
        rows.append(row)
        # The first four classes started and ended; the fifth never started.
        if offset <= 3:
            session = AttendanceSession(class_id=str(row.id), start_time=start,
                                        current_nonce=f"attendance-demo-{row.id}",
                                        nonce_expires_at=end, is_active=False, ended_at=end)
            db.session.add(session)
            db.session.flush()
            sessions.append(session)
        if offset == 7:
            db.session.add(CalendarClosure(date_from=day, date_to=day, scope="DEPARTMENT",
                                           department=DEPARTMENT,
                                           reason="Isolated attendance demo holiday",
                                           created_by=faculty.id))
    # Durable ids before any optional irreversible ledger write.
    db.session.commit()

    for index, session in enumerate(sessions[:3]):  # 3, then 2, then 1 captures
        for student_id in students[:3 - index]:
            tx_hash = None
            if args.allow_chain_writes:
                tx_hash = blockchain.lock_attendance(student_id, session.id)["transaction_hash"]
            db.session.add(AttendanceCheckIn(session_id=session.id, student_id=student_id,
                                             checked_in_at=session.start_time,
                                             onchain_tx_hash=tx_hash, method="qr",
                                             device_verified=False))
        db.session.commit()

    if args.allow_chain_writes:
        statuses = ("CONDUCTED", "CONDUCTED", "CONDUCTED", "CONDUCTED_ZERO_STUDENTS", "UNCONDUCTED")
        for row, status in zip(rows, statuses):
            workload.anchor_session_log(row, faculty.id, status)
            db.session.commit()

    print(f"Seeded: term={term.id}, section={section.id}, faculty={faculty.id} "
          f"({FACULTY_EMAIL}, demo password: {args.password}).")
    print("Log in as the demo faculty and request /faculty/attendance with "
          f"term={term.id}, section={section.id} over {anchor}..{anchor + timedelta(days=11)}.")
    for offset, expected in DAYS:
        print(f"   {anchor + timedelta(days=offset)}: {expected}")
    print("Percentages: 75.0%, 50.0%, 25.0% (3/4, 2/4, 1/4); the holiday is excluded from scheduled.")
    if args.allow_chain_writes:
        print("Chain mode: five SessionLog rows and one lock per capture were written permanently.")
    else:
        print("Local only: no chain call and no SessionLog. Verification will report one "
              "database-only capture per synthetic check-in; that is expected, not repaired.")
    print("Demo captures are synthetic: QR method, deviceVerified=false. No real identity checks.")
    print("HOD attendance reports return the same 409 for EVERY range while any unmapped "
          "slotless occurrence exists (Phase 2.9 legacy mappings). The faculty read is unaffected.")
    return 0


def report_existing(term: Term, args) -> int:
    """A complete seed is left unchanged; an incomplete one is never retried blindly."""
    rows = ScheduledClass.query.filter_by(term_id=term.id).order_by(ScheduledClass.start_time).all()
    class_ids = [row.id for row in rows]
    sessions = AttendanceSession.query.filter(
        AttendanceSession.class_id.in_([str(value) for value in class_ids])).all()
    captures = AttendanceCheckIn.query.filter(
        AttendanceCheckIn.session_id.in_([session.id for session in sessions])).count()
    offering = TermOffering.query.filter_by(term_id=term.id).first()
    closure = CalendarClosure.query.filter_by(
        department=DEPARTMENT, date_from=term.start_date + timedelta(days=7)).first()
    logs = SessionLog.query.filter(SessionLog.class_id.in_(class_ids)).count()
    owner = User.query.filter_by(email=FACULTY_EMAIL).first()
    complete = (owner is not None and len(rows) == 6 and len(sessions) == 4 and captures == 6 and
                offering is not None and closure is not None and logs in (0, 5) and
                all(row.professor_id == owner.id for row in rows))
    if not complete:
        raise ValueError(
            f"incomplete demo seed (rows={len(rows)}, sessions={len(sessions)}, "
            f"captures={captures}, logs={logs}); reconcile it before retrying")
    print(f"Existing complete seed: term={term.id}, section={offering.section_id}; unchanged "
          f"(logs={logs}, chain_mode={'yes' if logs else 'no'}).")
    if args.allow_chain_writes and logs != 5:
        print("This local-only seed has no ledger entries; chain mode cannot be added to it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
