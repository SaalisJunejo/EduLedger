"""Development utility: seed demo Grade rows (Phase 1.4a).

Why this exists
---------------
Grade corrections (POST /api/v1/grades/corrections) only work on an existing
Grade row. This script gives the first 3 STUDENT users (lowest user id first)
BSCS-401 = "B" and BSCS-402 = "C+" so the correction flow can be demoed
locally. It is idempotent: a (student, course) row that already exists is
left untouched, whatever its current value or lock.

Run it after students have registered:

    cd backend
    .\\venv\\Scripts\\python.exe seed_grades_demo.py

Development only - never run this against a database you care about.
"""

import os
import sys

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Grade, User, UserRole  # noqa: E402

_STUDENT_COUNT = 3
_DEMO_GRADES = [
    # (course_code, grade_value)
    ("BSCS-401", "B"),
    ("BSCS-402", "C+"),
]


def main() -> int:
    """Insert the demo grades that are missing; report every decision."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        print("EduLedger grades demo seed (development only)")
        print(f"  dialect: {db.engine.dialect.name}")

        students = (
            User.query.filter_by(role=UserRole.STUDENT)
            .order_by(User.id)
            .limit(_STUDENT_COUNT)
            .all()
        )
        if not students:
            print("  no STUDENT users yet - register students first; nothing seeded")
            return 0
        if len(students) < _STUDENT_COUNT:
            print(f"  only {len(students)} STUDENT user(s) found (wanted {_STUDENT_COUNT})")

        created = skipped = 0
        for student in students:
            for course_code, grade_value in _DEMO_GRADES:
                existing = Grade.query.filter_by(
                    student_id=student.id, course_code=course_code
                ).first()
                if existing is not None:
                    skipped += 1
                    lock = (
                        f", locked by proposal {existing.locked_by_proposal_id}"
                        if existing.locked_by_proposal_id is not None
                        else ""
                    )
                    print(
                        f"  skip   student #{student.id} {student.full_name:<24} "
                        f"{course_code} already {existing.grade_value!r}{lock}"
                    )
                    continue
                db.session.add(Grade(
                    student_id=student.id, course_code=course_code, grade_value=grade_value,
                ))
                created += 1
                print(
                    f"  insert student #{student.id} {student.full_name:<24} "
                    f"{course_code} = {grade_value!r}"
                )
        db.session.commit()
        print(f"[OK] {created} grade row(s) inserted, {skipped} already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
