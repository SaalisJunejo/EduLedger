"""Development utility: seed the Module 3 demo timetable (today's classes).

Why this exists
---------------
The faculty workload UI has no class-management surface yet (Phase 4+), so
the instructor dashboard's "My Classes" tab renders a mock roster whose ids
must match real ``scheduled_classes`` rows. This script creates them, in
the same spirit as the mock attendance class dropdown on InstructorPage.

It seeds:
- three classes for a real demo FACULTY user, with
  start times safely in the FUTURE so the background auto-flag job
  (UNCONDUCTED_THRESHOLD_MINUTES, default 15) cannot conclude them before
  the demo runs
- one already-overdue class for a second real FACULTY user, which the LIVE Flask
  server's scheduler auto-flags UNCONDUCTED on-chain within a minute —
  giving the admin workload audit log a realistic entry without touching
  the UI

The frontend mock lives in frontend/src/lib/workload.js (MOCK_CLASSES);
keep the ids/labels in sync with what this script prints.

Run it right after a fresh deploy + reset:

    cd backend
    py -3.12 reset_local_state.py
    py -3.12 seed_workload_demo.py

WorkloadLedger allows ONE entry per class id, forever. If the previous
demo already concluded classes, re-seeding alone is not enough — redeploy
the contracts (fresh chain) and run reset_local_state.py first, otherwise
scans/declarations for the re-issued ids will revert with
WorkloadAlreadyLogged. The script refuses to run over existing session
logs for exactly that reason.

Development only - never run this against a database you care about.
"""

import os
import sys
from datetime import timedelta

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402 (import after the env tweak)
from app.extensions import db  # noqa: E402
from app.models import FacultyProfile, ScheduledClass, SessionLog, User, UserRole  # noqa: E402
from app.services.auth import hash_password  # noqa: E402
from app.util import utcnow  # noqa: E402
from flask import current_app  # noqa: E402

# Local demo credentials only; existing accounts are never assigned new passwords.
DEMO_FACULTY_PASSWORD = "WorkloadDemoFaculty123!"
_DEMO_FACULTY = {
    "professor": ("faculty.workload.demo", "EMP-WORKLOAD-DEMO", "Demo Workload Professor"),
    "substitute": ("faculty.workload.substitute", "EMP-WORKLOAD-SUB", "Demo Workload Substitute"),
    "overdue": ("faculty.workload.overdue", "EMP-WORKLOAD-OVERDUE", "Demo Overdue Professor"),
}

# Mirrors the frontend mock roster, using account names resolved to real User ids.
_DEMO_CLASSES = [
    # (course_id, room, faculty account, start offset from now, in mock list?)
    ("BSCS-401", "A-101", "professor", timedelta(hours=3), True),
    ("BSCS-402", "B-204", "professor", timedelta(hours=6), True),
    ("BSCS-501", "C-301", "professor", timedelta(hours=9), True),
    ("MSCS-501", "D-202", "overdue", timedelta(minutes=-30), False),  # auto-flag demo
]


def ensure_demo_faculty() -> dict[str, User]:
    """Create/reuse real demo faculty and their profiles by email, in an app context."""
    domain = current_app.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    users = {}
    for name, (local_part, employee_id, full_name) in _DEMO_FACULTY.items():
        email = f"{local_part}@{domain}"
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                role=UserRole.FACULTY, email=email,
                password_hash=hash_password(DEMO_FACULTY_PASSWORD),
                full_name=full_name, department="Computer Science", is_active=True,
            )
            db.session.add(user)
            db.session.flush()
        elif user.role != UserRole.FACULTY:
            raise ValueError(f"demo account {email} exists but is not FACULTY")
        if user.faculty_profile is None:
            db.session.add(FacultyProfile(user_id=user.id, employee_id=employee_id))
        users[name] = user
    db.session.commit()
    return users


def main() -> int:
    """Seed today's demo timetable for the workload module."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        print("EduLedger workload demo seed (development only)")
        print(f"  dialect: {db.engine.dialect.name}")

        if SessionLog.query.count() > 0:
            print(
                "[REFUSED] session_logs is not empty - classes were already concluded "
                "on-chain. Redeploy the contracts on a fresh chain and run "
                "reset_local_state.py before re-seeding, or the re-issued class ids "
                "will collide with the ledger's one-entry-per-class rule."
            )
            return 1

        try:
            faculty = ensure_demo_faculty()
        except ValueError as exc:
            print(f"[REFUSED] {exc}")
            return 1
        for name, user in faculty.items():
            print(f"  {name:<16}: user #{user.id} · {user.email} · FACULTY")

        existing = ScheduledClass.query.count()
        if existing:
            print(f"  clearing {existing} leftover scheduled class row(s)")
            for model in (SessionLog, ScheduledClass):
                db.session.query(model).delete()
            db.session.commit()

        now = utcnow()
        print(f"  server time        : {now:%Y-%m-%d %H:%M} UTC")
        for course_id, room, faculty_name, offset, in_mock in _DEMO_CLASSES:
            professor_id = faculty[faculty_name].id
            scheduled_class = ScheduledClass(
                professor_id=professor_id,
                room=room,
                start_time=now + offset,
                course_id=course_id,
            )
            db.session.add(scheduled_class)
            db.session.commit()  # commit each row so ids are deterministic 1..4
            marker = "MOCK roster" if in_mock else "auto-flag demo (not in the mock roster)"
            print(
                f"  class #{scheduled_class.id}: {course_id:<8} Room {room} · "
                f"professor {professor_id} · starts {scheduled_class.start_time:%H:%M} "
                f"({offset}) · {marker}"
            )

        print("  next steps         : keep frontend/src/lib/workload.js MOCK_CLASSES")
        print("                       labels aligned with the start times above")
        print("[OK] demo timetable seeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
