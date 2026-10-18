"""Development utility: seed demo InstitutionWhitelist rows.

Why this exists
---------------
HOD registration requires verifying the applicant against the official
university directory (the institution whitelist). This script seeds 2-3
demo whitelist entries so HOD auto-verification and registration can be
tested locally without external directory infrastructure.

Run it right after setting up or resetting the database:

    cd backend
    py -3.12 seed_whitelist_demo.py

Development only - never run this against a database you care about.
"""

import os
import sys

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import InstitutionWhitelist  # noqa: E402

_DEMO_WHITELIST = [
    # (employee_id, department, name, email)
    ("EMP-HOD-CS-01", "Computer Science", "Dr. Tariq Mahmood", "tariq.cs@gcu.edu.pk"),
    ("EMP-HOD-EE-01", "Electrical Engineering", "Dr. Ayesha Khan", "ayesha.ee@gcu.edu.pk"),
    ("EMP-HOD-MATH-01", "Mathematics", "Dr. Imran Ali", "imran.math@gcu.edu.pk"),
]


def main() -> int:
    """Seed demo InstitutionWhitelist rows for HOD verification testing."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        print("EduLedger institution whitelist demo seed (development only)")
        print(f"  dialect: {db.engine.dialect.name}")

        existing = InstitutionWhitelist.query.count()
        if existing:
            print(f"  clearing {existing} leftover whitelist row(s)")
            db.session.query(InstitutionWhitelist).delete()
            db.session.commit()

        for employee_id, department, name, email in _DEMO_WHITELIST:
            entry = InstitutionWhitelist(
                employee_id=employee_id,
                department=department,
                name=name,
                email=email,
            )
            db.session.add(entry)
            db.session.commit()
            print(
                f"  whitelist #{entry.id}: {employee_id:<16} · "
                f"{name:<20} · {department:<24} · {email}"
            )

        print("[OK] demo institution whitelist seeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
