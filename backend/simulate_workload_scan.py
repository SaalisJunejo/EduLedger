"""Development utility: simulate a student scanning a faculty workload QR.

Why this exists
---------------
The workload module's "any single scan concludes the class" endpoint is
POST /api/v1/faculty/session/scan — a different endpoint than the Module 1
/attendance/scan the student app calls (that one locks the *student's*
attendance and does not conclude the class). Until the student app learns
to detect a workload QR (Phase 4+), this script stands in for that student
scan during demos: it reads the live rotating nonce exactly like the QR
payload would and submits it, which anchors the class CONDUCTED on-chain
and flips the instructor dashboard's badge via the logs poll.

    cd backend
    py -3.12 simulate_workload_scan.py            # newest live workload session
    py -3.12 simulate_workload_scan.py 14         # a specific session id

The QR payload is {"session_id": <id>, "nonce": "<current nonce>"} — the
same shape GET /api/v1/session/<id>/qr encodes, read here straight from the
projector endpoint so the nonce is always current (retried if it rotates
between the read and the submit).

Development only.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AttendanceSession, Student, User, UserRole  # noqa: E402
from seed_workload_demo import DEMO_FACULTY_PASSWORD, ensure_demo_faculty  # noqa: E402

BASE_URL = "http://localhost:5000/api/v1"


def _post(path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _login(email: str, password: str, role: str) -> tuple[str, dict]:
    """Obtain a real login token; never mint auth tokens inside the simulator."""
    status, body = _post("/auth/login", {"email": email, "password": password})
    if status != 200 or not body.get("token"):
        raise SystemExit(f"login failed for {email}: HTTP {status} - {body}")
    user = body.get("user") or {}
    if user.get("role") != role:
        raise SystemExit(f"{email} must be {role}, got {user.get('role')}")
    return body["token"], user


def _student_identity(app) -> tuple[str, int]:
    """Register/reuse a demo student by email and ensure Student.id == User.id."""
    domain = app.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    email = f"student.workload.demo@{domain}"
    password = "WorkloadDemoStudent123!"
    user = User.query.filter_by(email=email).first()
    if user is None:
        status, body = _post("/auth/register/student", {
            "email": email, "password": password, "full_name": "Demo Workload Student",
            "department": "Computer Science", "roll_number": "WORKLOAD-DEMO-STUDENT",
            "batch": "2022-2026",
        })
        if status != 201:
            raise SystemExit(f"student registration failed: HTTP {status} - {body}")
    elif user.role != UserRole.STUDENT:
        raise SystemExit(f"demo account {email} exists but is not STUDENT")
    token, identity = _login(email, password, "STUDENT")
    student_id = identity["id"]
    if db.session.get(Student, student_id) is None:
        db.session.add(Student(id=student_id, name=identity["fullName"]))
        db.session.commit()
    return token, student_id


def _newest_workload_session_id() -> int:
    """Id of the newest still-active workload-* attendance session."""
    session = (
        AttendanceSession.query.filter(
            AttendanceSession.class_id.like("workload-%"),
            AttendanceSession.is_active.is_(True),
        )
        .order_by(AttendanceSession.id.desc())
        .first()
    )
    if session is None:
        raise SystemExit("no active workload session - start one from the UI first")
    return session.id


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        session_id = int(sys.argv[1]) if len(sys.argv) > 1 else _newest_workload_session_id()
        faculty = ensure_demo_faculty()["professor"]
        faculty_token, professor = _login(faculty.email, DEMO_FACULTY_PASSWORD, "FACULTY")
        if professor["id"] != faculty.id:
            raise SystemExit("faculty login id does not match the demo faculty User.id")
        student_token, student_id = _student_identity(app)
        print(
            f"Simulating student #{student_id} scanning workload session #{session_id} "
            f"(projector auth: faculty #{professor['id']})"
        )

        # The nonce rotates every NONCE_ROTATION_SECONDS (5s); read and
        # submit immediately, retrying a couple of times if it rotates
        # in between (the real QR would simply be rescanned).
        for attempt in range(1, 4):
            qr = _get(f"/session/{session_id}/qr", faculty_token)
            status, body = _post(
                "/faculty/session/scan",
                {"session_id": session_id, "nonce": qr["current_nonce"]},
                token=student_token,
            )
            if status in (200, 201):
                print(f"[OK] HTTP {status} - {body.get('status')}")
                print(f"     class          : {body['session_log']['classId']}")
                print(f"     conducted by   : {body['session_log']['conductedBy']}")
                print(f"     transaction    : {body.get('transaction_hash')}")
                return 0
            if body.get("error") == "nonce_mismatch":
                print(f"  attempt {attempt}: nonce rotated - resubmitting")
                continue
            print(f"[FAILED] HTTP {status} - {body}")
            return 1

        print("[FAILED] nonce kept rotating - try again")
        return 1


if __name__ == "__main__":
    sys.exit(main())
