r"""Development-only, idempotent timetable master seed (Phase 2.2).

Run from backend/ after applying the Phase 2.2 SQL:
    .\venv\Scripts\python.exe seed_timetable_demo.py

Reuses an existing active term; otherwise activates Demo Term. Inserts only
missing demo master rows and blank offerings. Never copies a pattern, writes
an occurrence, modifies a contract or calls the chain. No background jobs.
"""

import os
import sys
from datetime import date

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Course, Room, Section, Term, TermOffering  # noqa: E402
from app.services.timetable import activate_term, seed_periods  # noqa: E402

DEPARTMENT = "Computer Science"
DEMO_COURSES = (
    ("CS101", "Programming Fundamentals", False),
    ("CS101L", "Programming Fundamentals Lab", True),
    ("CS201", "Data Structures", False),
    ("CS201L", "Data Structures Lab", True),
    ("CS301", "Database Systems", False),
    ("CS401", "Operating Systems", False),
)
DEMO_ROOMS = (
    ("Room 3", "LECTURE", 50),
    ("Room 6", "LECTURE", 50),
    ("Lab 5", "LAB", 30),
    ("Lab 6", "LAB", 30),
)


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    if os.getenv("FLASK_CONFIG", "development") != "development":
        print("Refused: timetable demo seeding is development-only.")
        return 1
    app = create_app("development")
    with app.app_context():
        messages = []
        created = 0
        try:
            # The factory already seeds the grid. A repeated call creates nothing.
            periods_created = seed_periods()
            for year in (2023, 2024):
                for letter in ("A", "B"):
                    row = Section.query.filter_by(programme="BS CS", batch_year=year, letter=letter).first()
                    if row is None:
                        row = Section(programme="BS CS", batch_year=year, letter=letter, department=DEPARTMENT)
                        db.session.add(row)
                        created += 1
                        messages.append(f"created section BS CS {year} {letter}")
                    else:
                        if row.department != DEPARTMENT:
                            raise ValueError(f"section BS CS {year} {letter} belongs to another department")
                        messages.append(f"existing section BS CS {year} {letter}")
            for code, title, is_lab in DEMO_COURSES:
                row = Course.query.filter(db.func.lower(Course.code) == code.lower()).first()
                if row is None:
                    db.session.add(Course(code=code, title=title, department=DEPARTMENT, is_lab=is_lab))
                    created += 1
                    messages.append(f"created course {code} ({'LAB' if is_lab else 'LECTURE'})")
                else:
                    if row.department != DEPARTMENT or row.is_lab != is_lab:
                        raise ValueError(f"course {code} has conflicting department or lab type; left untouched")
                    messages.append(f"existing course {code}")
            for name, room_type, capacity in DEMO_ROOMS:
                row = Room.query.filter(db.func.lower(Room.name) == name.lower()).first()
                if row is None:
                    db.session.add(Room(name=name, type=room_type, block="CS", capacity=capacity, department=DEPARTMENT))
                    created += 1
                    messages.append(f"created room {name} ({room_type})")
                else:
                    if row.department != DEPARTMENT or row.type != room_type:
                        raise ValueError(f"room {name} has conflicting department or type; left untouched")
                    messages.append(f"existing room {name}")
            term = Term.query.filter_by(is_active=True).first()
            if term is None:
                term = Term.query.filter_by(
                    name="Demo Term", start_date=date(2026, 9, 1), end_date=date(2027, 1, 31),
                ).first()
                if term is None:
                    term = Term(name="Demo Term", start_date=date(2026, 9, 1), end_date=date(2027, 1, 31), is_active=False)
                    db.session.add(term)
                    db.session.flush()
                    created += 1
                    messages.append(f"created term {term.name}")
                else:
                    messages.append(f"existing term {term.name}")
            else:
                messages.append(f"reusing active term {term.name} (#{term.id})")
            before = {row.section_id for row in TermOffering.query.filter_by(term_id=term.id).all()}
            offering_count, _ = activate_term(term)
            for row in TermOffering.query.filter_by(term_id=term.id).order_by(TermOffering.section_id):
                if row.section_id not in before:
                    messages.append(f"created blank offering: term #{term.id}, {row.section.to_dict()['label']}")
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f"FAIL: {type(exc).__name__}: {exc}; demo master changes rolled back")
            return 1
        print("EduLedger timetable demo seed (development only)")
        for message in messages:
            print(f"  {message}")
        print(f"[OK] {created} master rows, {offering_count} offerings, {periods_created} additional periods created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
