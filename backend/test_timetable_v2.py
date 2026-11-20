"""Phase 2.1 workload, 2.2 master-data, 2.3 directory and 2.4 pattern tests.

Run it against a live stack (from backend/), after restarting Flask so it
serves the Phase 2.4 code (apply all timetable migration SQL first):

    .\\venv\\Scripts\\python.exe test_timetable_v2.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - WorkloadLedger deployed     (contracts/: npm run deploy:workload)
    - Flask backend running       (backend/: python run.py)
    - Phase 2.3 SQL applied and Redis running
    - Legacy steps 0-23 need GRANDFATHER_OPEN_REGISTRATION=true on the
      live TEST server; this script does not turn on that flag.

Steps:
    0. preflight          - register + log in unique professor, substitute,
                            student and HOD accounts; GET /health
    1. seed classes       - four absolute-time ScheduledClass rows (see below)
    2. too_late (attendance) - POST /session/start after end_time      -> 403
    3. too_late (workload)   - POST /faculty/session/start after end_time -> 403
                            too_late (or 409 class_already_logged, see notes)
    4. too_late (substitute) - POST /faculty/substitute/redeem after end_time -> 403
                            too_late (or 409 class_already_logged, see notes)
    5. late start         - workload start 15 min after start_time, zero-attendance;
                            /faculty/logs -> late=true, lateMinutes 14-16
    6. on-time start      - workload start at start_time, one student scan;
                            /faculty/logs -> late=false
    7. not auto-flagged   - a class whose end_time has not passed is not
                            selected by the auto-flag job (and is at end_time + 1 min)
    8. ended, no session  - flag_unconducted_classes() called directly ->
                            UNCONDUCTED on-chain + local SessionLog
    9. ended, started     - a session was started -> not selected for UNCONDUCTED;
                            flag_unconducted_classes() resolves it
                            CONDUCTED_ZERO_STUDENTS, exactly one SessionLog
   10. legacy null end    - end_time=None -> threshold rule still selects and
                            flags it (threshold overridden to 1 min in-process)
   11. late, ended empty  - workload start 15 min late, POST /session/<id>/end with
                            zero captures -> CONDUCTED_ZERO_STUDENTS on-chain,
                            /faculty/logs late=true, lateMinutes 14-16 (Phase 2.1c)
   12. ended twice        - a second /end writes no second entry
   13. one capture        - ending sessions that captured someone (a workload scan,
                            a seeded check-in) writes no zero-student entry;
                            the check-in class gets exactly one CONDUCTED entry
   14. attendance only    - POST /session/start, one check-in, /end -> exactly one
                            CONDUCTED entry on-chain + /faculty/logs with
                            late/lateMinutes (Phase 2.1e)
   15. attendance twice   - /end again on that session -> still one entry
   16. attendance empty   - POST /session/start, zero check-ins, /end ->
                            CONDUCTED_ZERO_STUDENTS (the 2.1c path)
   17. already logged     - a check-in + /end on a class the workload scan already
                            logged -> no second entry
   18. sweep resolves     - end_time passed, ended attendance session with one
                            check-in, no SessionLog -> flag_unconducted_classes()
                            anchors CONDUCTED (not UNCONDUCTED)
   19. term activation    - unique ADMIN/master fixtures; activation creates
                            exactly one blank offering per existing section
   20. activate twice     - no new offerings, same offering ids
   21. HOD scope          - cross-department section/course/room writes ->
                            403 timetable/out_of_department, unchanged rows
   22. seed periods       - repeated seed creates nothing; exact grid retained
   23. break period       - one 11:10-11:30 break with isBreak=true
   24-34. directory       - atomic/idempotent import, dry run, scoped listing,
                            logged code delivery, wrong/expired/rate-limited
                            codes, permanent membership, claim and dev guards.
                            These steps use APP.test_client against the same
                            DB/Redis to capture logs and scope the 1s TTL.
                            Only IPFS pinning is mocked for these new steps.
   35-45. weekly pattern  - atomic/idempotent import, merged labs, whole-term
                            teacher/room clashes, section/mismatch warnings,
                            break/legacy rejection, dry run, HOD scope and copy.
                            Local Flask requests; no occurrence generation.

Notes:
    - SCHEDULER_ENABLED=false is set before importing `app`, so this process
      never hosts a second scheduler. The auto-flag selection is checked
      in-process via app.scheduler.overdue_classes(); step 7 anchors
      nothing. Every step that depends on the sweep (8, 9, 10, 18) calls
      flag_unconducted_classes() directly, which also flags or resolves any
      other eligible row in the shared DB (as the live job would; in step 10
      with the 1-minute threshold, like test_workload.py). If the live job
      gets to one of those classes first, the outcome asserted is the same.
      For a fully deterministic run, start the server with
      SCHEDULER_ENABLED=false too.
    - The too_late class already ended and carries one ended attendance
      session (zero check-ins), so D19 never flags it UNCONDUCTED - but the
      live server's sweep resolves it CONDUCTED_ZERO_STUDENTS (Phase 2.1e)
      once it ticks. Workload start and substitute redeem check the
      SessionLog before the start gate, so steps 3-4 accept either too_late
      or class_already_logged; both still mean a refused start.
    - The step 13 check-in is seeded directly (one AttendanceCheckIn row, as
      /attendance/scan would mirror it); the face/device scan path is not
      exercised here. It is deleted in cleanup before its session. Steps
      14, 17 and 18 seed their check-ins the same way; step 18 writes the
      class, its ended session and the check-in in one commit, so the live
      sweep can only ever resolve it as CONDUCTED.
    - Cleanup deletes only this run's rows (users by unique email, their
      classes, sessions, tokens and logs). On-chain entries are append-only
      and stay on the local Hardhat chain. Phase 2.2 cleanup also deletes this
      run's offerings/master rows and restores the previously active term
      without creating offerings in it. Periods are shared and left intact.
"""

import csv
import io
import logging
import os
import sys
import time
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

# Must be set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

import requests  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    AttendanceCheckIn, AttendanceSession, Course, DirectoryEntry, FacultyProfile, InstitutionWhitelist, Period,
    Room, ScheduledClass, Section, SessionLog, Student, StudentProfile, StudentSection, SubstituteToken, Term,
    TermOffering, TimetableSlot, User, UserRole, WorkloadSession,
)
from app.scheduler import flag_unconducted_classes, overdue_classes  # noqa: E402
from app.services import registration_code, workload  # noqa: E402
from app.services.directory import COLUMNS as DIRECTORY_COLUMNS  # noqa: E402
from app.services.auth import hash_password  # noqa: E402
from app.services.timetable import seed_periods  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from app.util import utcnow  # noqa: E402

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"
RUN_ID = uuid4().hex
RUN_STARTED = utcnow()
PASSWORD = "TimetableV2Password123!"

# A short legacy threshold for step 10 (minutes); only this process uses it.
TEST_THRESHOLD_MINUTES = 1

APP = None  # initialized inside preflight, not as an import side effect
ACCOUNTS = {}
AUTH = {}
STATE = {
    "class_ids": [],
    "too_late_class_id": None,
    "late_class_id": None,
    "on_time_class_id": None,
    "not_ended_class_id": None,
    "fixture_session_id": None,
    "seeded_session_ids": [],
    "late_end_class_id": None,
    "late_end_session_id": None,
    "late_end_tx_hash": None,
    "attendance_class_id": None,
    "attendance_session_id": None,
    "attendance_tx_hash": None,
    "student_row_owned": False,
    "master_section_ids": [],
    "master_course_ids": [],
    "master_room_ids": [],
    "master_term_id": None,
    "previous_active_term_id": None,
    "term_activation_attempted": False,
    "offering_ids": [],
    "foreign_resources": [],
    "directory_rows": [],
    "directory_section_ids": [],
    "directory_ids": [],
    "directory_codes": {},
    "directory_success": None,
    "pattern_rows": [],
    "pattern_courses": [],
    "pattern_rooms": [],
    "pattern_copy_term_ids": [],
}


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=15, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def assert_too_late(status: int, payload: dict, route: str) -> None:
    """The D19 start-gate rejection, identical on every start path."""
    assert status == 403, f"{route} after end_time must be rejected with 403, got {status}: {payload}"
    assert payload.get("check") == "schedule", f"{route}: unexpected check: {payload}"
    assert payload.get("error") == "too_late", (
        f"{route}: expected too_late (restart Flask to load Phase 2.1?): {payload}"
    )
    print(f"   {route} rejected at check '{payload['check']}': {payload['message']}")


def assert_refused_start(status: int, payload: dict, route: str) -> None:
    """too_late, or class_already_logged if the live sweep resolved the class first."""
    # Race with the live sweep: it may resolve the ended too_late class as
    # CONDUCTED_ZERO_STUDENTS mid-run, and these routes check the SessionLog
    # before the start gate. Either answer is a refused start.
    if payload.get("error") == "class_already_logged":
        assert status == 409, f"{route}: class_already_logged must be 409, got {status}: {payload}"
        assert payload.get("check") == "session_log", f"{route}: unexpected check: {payload}"
        print(f"   {route} rejected at check '{payload['check']}' (live sweep won the race)")
        return
    assert_too_late(status, payload, route)


def assert_no_new_session(class_id: int) -> None:
    """No attendance session (plain or workload) was opened for the class by this run."""
    with APP.app_context():
        opened = AttendanceSession.query.filter(
            AttendanceSession.class_id.in_([str(class_id), f"workload-{class_id}"]),
            AttendanceSession.start_time >= RUN_STARTED,
        ).count()
    assert opened == 0, f"a refused start opened {opened} attendance session(s) for class {class_id}"


def log_for(class_id: int) -> dict | None:
    """The SessionLog entry for a class from GET /faculty/logs (or None)."""
    status, payload = api("GET", "/faculty/logs", token=AUTH["hod_token"])
    assert status == 200, f"GET /faculty/logs returned {status}: {payload}"
    for entry in payload.get("logs") or []:
        if entry.get("classId") == class_id:
            return entry
    return None


def seed_class(
    start_offset: timedelta, end_offset: timedelta | None, course: str, started: bool = False,
) -> int:
    """One class owned by this run's professor; end_offset None -> null end_time.

    `started` adds an ended attendance session in the same commit, so the
    live auto-flag job never sees the class without it.
    """
    with APP.app_context():
        now = utcnow()
        scheduled_class = ScheduledClass(
            professor_id=AUTH["professor_id"],
            room="TT2-ROOM",
            start_time=now + start_offset,
            end_time=None if end_offset is None else now + end_offset,
            day_of_week=None,
            course_code=course,
        )
        db.session.add(scheduled_class)
        db.session.flush()
        session = None
        if started:
            session = AttendanceSession(
                class_id=str(scheduled_class.id),
                start_time=now + start_offset + timedelta(minutes=2),
                is_active=False,
                ended_at=now + start_offset + timedelta(minutes=30),
            )
            session.rotate_nonce(now=now, ttl_seconds=APP.config["ATTENDANCE_ROTATION_SECONDS"])
            db.session.add(session)
        db.session.commit()
        STATE["class_ids"].append(scheduled_class.id)
        if session is not None:
            STATE["seeded_session_ids"].append(session.id)
        return scheduled_class.id


def assert_flagged_unconducted(class_id: int) -> str:
    """UNCONDUCTED in the local log and on-chain, credited to the professor."""
    entry = log_for(class_id)
    assert entry is not None, f"no SessionLog for the overdue class {class_id} after the job ran"
    assert entry.get("status") == "UNCONDUCTED", f"auto-flag status wrong: {entry}"
    assert entry.get("conductedBy") == AUTH["professor_id"], f"auto-flag credit wrong: {entry}"
    assert str(entry.get("onchainTxHash", "")).startswith("0x"), f"no tx hash on the auto-flag entry: {entry}"
    with APP.app_context():
        onchain = workload.get_workload_log(class_id)
    assert onchain["logged"] is True and onchain["status"] == "UNCONDUCTED", f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == AUTH["professor_id"], f"on-chain credit wrong: {onchain}"
    return entry["onchainTxHash"]


def start_workload(class_id: int) -> dict:
    """Professor starts the workload session; returns the response body."""
    status, payload = api(
        "POST", "/faculty/session/start", token=AUTH["professor_token"], json={"class_id": class_id},
    )
    assert status == 201, f"/faculty/session/start returned {status}: {payload}"
    return payload


def end_session(session_id: int) -> dict:
    """Professor ends a session via POST /session/<id>/end; returns the body."""
    status, payload = api("POST", f"/session/{session_id}/end", token=AUTH["professor_token"], json={})
    assert status == 200, f"/session/{session_id}/end returned {status}: {payload}"
    assert payload["session"]["is_active"] is False, f"session still active after /end: {payload}"
    return payload


def log_count(class_id: int) -> int:
    """Local SessionLog rows for a class (one entry per class is the rule)."""
    with APP.app_context():
        return SessionLog.query.filter_by(class_id=class_id).count()


def start_attendance(class_id: int) -> int:
    """Professor starts a plain attendance session; returns its id."""
    status, payload = api(
        "POST", "/session/start", token=AUTH["professor_token"], json={"class_id": class_id},
    )
    assert status == 201, f"/session/start returned {status}: {payload}"
    return payload["session"]["id"]


def seed_check_in(session_id: int) -> None:
    """One verified capture, as /attendance/scan would mirror it locally."""
    with APP.app_context():
        db.session.add(AttendanceCheckIn(
            session_id=session_id, student_id=AUTH["student_id"], onchain_tx_hash="0x" + "0" * 64,
        ))
        db.session.commit()


def seed_ended_with_check_in(course: str) -> int:
    """An ended class with one ended attendance session and one check-in.

    All three rows land in one commit, so the live sweep never sees the class
    with a session but without its capture.
    """
    with APP.app_context():
        now = utcnow()
        start = now - timedelta(minutes=60)
        scheduled_class = ScheduledClass(
            professor_id=AUTH["professor_id"], room="TT2-ROOM", start_time=start,
            end_time=now - timedelta(seconds=30), day_of_week=None, course_code=course,
        )
        db.session.add(scheduled_class)
        db.session.flush()
        session = AttendanceSession(
            class_id=str(scheduled_class.id),
            start_time=start + timedelta(minutes=2),
            is_active=False,
            ended_at=start + timedelta(minutes=30),
        )
        session.rotate_nonce(now=now, ttl_seconds=APP.config["ATTENDANCE_ROTATION_SECONDS"])
        db.session.add(session)
        db.session.flush()
        db.session.add(AttendanceCheckIn(
            session_id=session.id, student_id=AUTH["student_id"], onchain_tx_hash="0x" + "0" * 64,
        ))
        db.session.commit()
        STATE["class_ids"].append(scheduled_class.id)
        STATE["seeded_session_ids"].append(session.id)
        return scheduled_class.id


def assert_resolved(class_id: int, status: str) -> dict:
    """`status` locally and on-chain: professor credited, professor conducted."""
    entry = log_for(class_id)
    assert entry is not None, f"class {class_id} missing from /faculty/logs"
    assert entry.get("status") == status, f"expected {status}: {entry}"
    assert entry.get("professorId") == AUTH["professor_id"], f"credit wrong: {entry}"
    assert entry.get("conductedBy") == AUTH["professor_id"], f"conductor wrong: {entry}"
    assert str(entry.get("onchainTxHash", "")).startswith("0x"), f"no tx hash: {entry}"
    with APP.app_context():
        onchain = workload.get_workload_log(class_id)
    assert onchain["logged"] is True and onchain["status"] == status, f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == AUTH["professor_id"], f"on-chain credit wrong: {onchain}"
    return entry


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Register/login unique users, then check DB + RPC + workload ledger."""
    global APP
    APP = create_app()
    domain = APP.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    for persona in ("professor", "substitute", "student", "hod"):
        account = {
            "email": f"ttv2.{persona}.{RUN_ID}@{domain}", "password": PASSWORD,
            "full_name": f"Timetable V2 {persona.title()}", "department": "Computer Science",
        }
        if persona == "student":
            account.update(roll_number=f"TT2-STUDENT-{RUN_ID}", batch="2022-2026")
        else:
            account["employee_id"] = f"TT2-{persona.upper()}-{RUN_ID}"
        ACCOUNTS[persona] = account
    with APP.app_context():
        hod = ACCOUNTS["hod"]
        db.session.add(InstitutionWhitelist(
            employee_id=hod["employee_id"], email=hod["email"],
            name=hod["full_name"], department=hod["department"],
        ))
        db.session.commit()
    for persona, account in ACCOUNTS.items():
        role = persona if persona in ("student", "hod") else "faculty"
        status, payload = api("POST", f"/auth/register/{role}", json=account)
        assert status == 201, f"{persona} registration failed ({status}): {payload}"
        status, payload = api("POST", "/auth/login", json={"email": account["email"], "password": PASSWORD})
        assert status == 200, f"{persona} login failed ({status}): {payload}"
        assert payload.get("token"), f"{persona} login returned no token: {payload}"
        AUTH[f"{persona}_token"] = payload["token"]
        AUTH[f"{persona}_id"] = payload["user"]["id"]
    with APP.app_context():
        student_id = AUTH["student_id"]
        assert db.session.get(Student, student_id) is None, (
            f"student id {student_id} already exists; do not overwrite unrelated attendance data"
        )
        db.session.add(Student(id=student_id, name=ACCOUNTS["student"]["full_name"]))
        db.session.commit()
        STATE["student_row_owned"] = True

    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    assert deps["contracts_configured"]["workload_ledger"], "workload ledger contract address is not configured"
    print(
        f"   professor={AUTH['professor_id']} substitute={AUTH['substitute_id']} "
        f"student={AUTH['student_id']} hod={AUTH['hod_id']} "
        f"LATE_AFTER_MINUTES={APP.config['LATE_AFTER_MINUTES']}"
    )


def step_seed_classes():
    """Four absolute-time classes owned by this run's professor."""
    with APP.app_context():
        now = utcnow()

        def seed(start_offset: timedelta, end_offset: timedelta, course: str) -> int:
            scheduled_class = ScheduledClass(
                professor_id=AUTH["professor_id"],
                room="TT2-ROOM",
                start_time=now + start_offset,
                end_time=now + end_offset,
                day_of_week=None,
                course_code=course,
            )
            db.session.add(scheduled_class)
            db.session.commit()
            STATE["class_ids"].append(scheduled_class.id)
            return scheduled_class.id

        too_late = seed(timedelta(minutes=-60), timedelta(minutes=-10), "TT2-LATE-GATE")
        STATE["too_late_class_id"] = too_late
        STATE["late_class_id"] = seed(timedelta(minutes=-15), timedelta(minutes=35), "TT2-LATE")
        STATE["on_time_class_id"] = seed(timedelta(seconds=-5), timedelta(minutes=50), "TT2-ON-TIME")
        STATE["not_ended_class_id"] = seed(timedelta(minutes=-20), timedelta(minutes=30), "TT2-NOT-ENDED")

        # An ended session for the too_late class keeps the live auto-flag
        # job away from it (see module docstring).
        fixture = AttendanceSession(
            class_id=str(too_late),
            start_time=now + timedelta(minutes=-59),
            is_active=False,
            ended_at=now + timedelta(minutes=-11),
        )
        fixture.rotate_nonce(now=now, ttl_seconds=APP.config["ATTENDANCE_ROTATION_SECONDS"])
        db.session.add(fixture)
        db.session.commit()
        STATE["fixture_session_id"] = fixture.id
    print(
        f"   too_late={STATE['too_late_class_id']} late={STATE['late_class_id']} "
        f"on_time={STATE['on_time_class_id']} not_ended={STATE['not_ended_class_id']}"
    )


def step_too_late_attendance():
    """POST /session/start after end_time -> too_late."""
    status, payload = api(
        "POST", "/session/start", token=AUTH["professor_token"],
        json={"class_id": STATE["too_late_class_id"]},
    )
    assert_too_late(status, payload, "/session/start")


def step_too_late_workload():
    """POST /faculty/session/start after end_time -> too_late (or class_already_logged)."""
    status, payload = api(
        "POST", "/faculty/session/start", token=AUTH["professor_token"],
        json={"class_id": STATE["too_late_class_id"]},
    )
    assert_refused_start(status, payload, "/faculty/session/start")
    assert_no_new_session(STATE["too_late_class_id"])


def step_too_late_redeem():
    """A valid substitute token redeemed after end_time -> too_late (or class_already_logged)."""
    status, payload = api(
        "POST", "/faculty/substitute/issue", token=AUTH["professor_token"],
        json={"class_id": STATE["too_late_class_id"], "substitute_id": AUTH["substitute_id"]},
    )
    assert status == 201, f"/faculty/substitute/issue returned {status}: {payload}"
    status, payload = api(
        "POST", "/faculty/substitute/redeem", token=AUTH["substitute_token"],
        json={"token": payload["substitute_token"]},
    )
    assert_refused_start(status, payload, "/faculty/substitute/redeem")
    with APP.app_context():
        assert WorkloadSession.query.filter_by(class_id=STATE["too_late_class_id"]).count() == 0, (
            "a rejected start must not open a workload session"
        )
    assert_no_new_session(STATE["too_late_class_id"])


def step_late_start():
    """Start 15 minutes after start_time -> late=true, lateMinutes 14-16."""
    class_id = STATE["late_class_id"]
    start_workload(class_id)
    status, payload = api(
        "POST", "/faculty/session/zero-attendance", token=AUTH["professor_token"], json={"class_id": class_id},
    )
    assert status == 201, f"/faculty/session/zero-attendance returned {status}: {payload}"
    entry = log_for(class_id)
    assert entry is not None, f"class {class_id} missing from /faculty/logs"
    assert entry.get("late") is True, f"expected late=true: {entry}"
    minutes = entry.get("lateMinutes")
    assert isinstance(minutes, int) and 14 <= minutes <= 16, f"lateMinutes out of 14-16: {entry}"
    print(f"   status={entry['status']} late={entry['late']} lateMinutes={minutes}")


def step_on_time_start():
    """Start at start_time, one student scan -> late=false."""
    class_id = STATE["on_time_class_id"]
    session_id = start_workload(class_id)["session"]["id"]
    for _attempt in range(2):  # the QR may rotate between fetch and scan
        status, qr = api("GET", f"/session/{session_id}/qr", token=AUTH["professor_token"])
        assert status == 200, f"GET /session/{session_id}/qr returned {status}: {qr}"
        status, payload = api(
            "POST", "/faculty/session/scan", token=AUTH["student_token"],
            json={"session_id": session_id, "nonce": qr["current_nonce"]},
        )
        if payload.get("check") != "session_nonce":
            break
    assert status == 201, f"/faculty/session/scan returned {status}: {payload}"
    entry = log_for(class_id)
    assert entry is not None, f"class {class_id} missing from /faculty/logs"
    assert entry.get("late") is False, f"expected late=false: {entry}"
    minutes = entry.get("lateMinutes")
    assert isinstance(minutes, int) and minutes <= APP.config["LATE_AFTER_MINUTES"], (
        f"lateMinutes not an on-time value: {entry}"
    )
    print(f"   status={entry['status']} late={entry['late']} lateMinutes={minutes}")


def step_not_auto_flagged():
    """end_time not yet passed (start + 20 min, no session) -> not selected."""
    class_id = STATE["not_ended_class_id"]
    with APP.app_context():
        scheduled_class = db.session.get(ScheduledClass, class_id)
        selected = {row.id for row in overdue_classes(APP, utcnow())}
        assert class_id not in selected, f"class {class_id} selected before its end_time"
        # Control: the same class IS selected once its end_time has passed.
        later = scheduled_class.end_time + timedelta(minutes=1)
        selected = {row.id for row in overdue_classes(APP, later)}
        assert class_id in selected, f"class {class_id} not selected after its end_time (control)"
        assert SessionLog.query.filter_by(class_id=class_id).count() == 0, (
            f"class {class_id} was logged before its end_time (live server not on Phase 2.1?)"
        )
    print(f"   class {class_id}: not selected now, selected at end_time + 1 min, no SessionLog")


def step_ended_no_session_flagged():
    """end_time passed, no session -> UNCONDUCTED on-chain + local SessionLog."""
    class_id = seed_class(timedelta(minutes=-60), timedelta(seconds=-30), "TT2-ENDED-EMPTY")
    flag_unconducted_classes(APP)
    tx_hash = assert_flagged_unconducted(class_id)
    print(f"   class {class_id} (ended, no session) flagged UNCONDUCTED on-chain: tx={tx_hash}")


def step_ended_with_session_not_flagged():
    """end_time passed but a session was started -> zero-student, not UNCONDUCTED."""
    class_id = seed_class(timedelta(minutes=-60), timedelta(seconds=-30), "TT2-ENDED-STARTED", started=True)
    with APP.app_context():
        selected = {row.id for row in overdue_classes(APP, utcnow())}
    assert class_id not in selected, f"class {class_id} with a started session was selected"
    flag_unconducted_classes(APP)
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = assert_resolved(class_id, "CONDUCTED_ZERO_STUDENTS")
    print(
        f"   class {class_id} (ended, session started): not selected, "
        f"resolved {entry['status']}, tx={entry['onchainTxHash']}"
    )


def step_legacy_null_end_time_flagged():
    """end_time=None, no session -> still flagged by the threshold rule."""
    class_id = seed_class(timedelta(minutes=-3), None, "TT2-LEGACY")
    with APP.app_context():
        scheduled_class = db.session.get(ScheduledClass, class_id)
        threshold = timedelta(minutes=APP.config["UNCONDUCTED_THRESHOLD_MINUTES"])
        due = scheduled_class.start_time + threshold
        # The configured threshold, not an end_time, decides selection.
        before = {row.id for row in overdue_classes(APP, due - timedelta(seconds=30))}
        after = {row.id for row in overdue_classes(APP, due + timedelta(seconds=30))}
    assert class_id not in before, f"class {class_id} selected before startTime + threshold"
    assert class_id in after, f"class {class_id} not selected after startTime + threshold"
    original = APP.config["UNCONDUCTED_THRESHOLD_MINUTES"]
    APP.config["UNCONDUCTED_THRESHOLD_MINUTES"] = TEST_THRESHOLD_MINUTES
    try:
        flag_unconducted_classes(APP)
    finally:
        APP.config["UNCONDUCTED_THRESHOLD_MINUTES"] = original
    tx_hash = assert_flagged_unconducted(class_id)
    print(
        f"   class {class_id} (no end_time, startTime now - 3 min, threshold "
        f"{TEST_THRESHOLD_MINUTES} min) flagged UNCONDUCTED on-chain: tx={tx_hash}"
    )


def step_late_ended_empty():
    """Start 15 min late, end with zero captures -> zero-student outcome, late."""
    class_id = seed_class(timedelta(minutes=-15), timedelta(minutes=35), "TT2-LATE-END")
    STATE["late_end_class_id"] = class_id
    session_id = start_workload(class_id)["session"]["id"]
    STATE["late_end_session_id"] = session_id
    payload = end_session(session_id)
    assert payload.get("count") == 0, f"expected zero captures in the /end response: {payload}"

    entry = log_for(class_id)
    assert entry is not None, f"class {class_id} unresolved after a zero-capture end (no SessionLog)"
    assert entry.get("status") == "CONDUCTED_ZERO_STUDENTS", f"expected the zero-student variant: {entry}"
    assert entry.get("professorId") == AUTH["professor_id"], f"credit wrong: {entry}"
    assert entry.get("conductedBy") == AUTH["professor_id"], f"conductor wrong: {entry}"
    assert str(entry.get("onchainTxHash", "")).startswith("0x"), f"no tx hash: {entry}"
    assert entry.get("late") is True, f"expected late=true: {entry}"
    minutes = entry.get("lateMinutes")
    assert isinstance(minutes, int) and 14 <= minutes <= 16, f"lateMinutes out of 14-16: {entry}"
    with APP.app_context():
        onchain = workload.get_workload_log(class_id)
    assert onchain["logged"] is True and onchain["status"] == "CONDUCTED_ZERO_STUDENTS", (
        f"on-chain state wrong: {onchain}"
    )
    assert onchain["professor_id"] == AUTH["professor_id"], f"on-chain credit wrong: {onchain}"
    STATE["late_end_tx_hash"] = entry["onchainTxHash"]
    print(
        f"   class {class_id}: status={entry['status']} late={entry['late']} "
        f"lateMinutes={minutes} tx={entry['onchainTxHash']}"
    )


def step_ended_twice():
    """A second /end on the same session writes no second entry."""
    class_id = STATE["late_end_class_id"]
    end_session(STATE["late_end_session_id"])
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = log_for(class_id)
    assert entry is not None and entry.get("onchainTxHash") == STATE["late_end_tx_hash"], (
        f"the entry changed after a second /end: {entry}"
    )
    print(f"   class {class_id}: still one entry, tx={entry['onchainTxHash']}")


def step_capture_unaffected():
    """Ending sessions that captured someone writes no zero-student entry."""
    # (a) The on-time class was concluded by one workload scan (step 6).
    on_time = STATE["on_time_class_id"]
    with APP.app_context():
        workload_session = WorkloadSession.query.filter_by(class_id=on_time).order_by(
            WorkloadSession.id.desc(),
        ).first()
        assert workload_session is not None, f"no workload session for class {on_time}"
        session_id = workload_session.attendance_session_id
    end_session(session_id)
    assert log_count(on_time) == 1, f"class {on_time} has {log_count(on_time)} SessionLog rows, expected 1"
    entry = log_for(on_time)
    assert entry is not None and entry.get("status") == "CONDUCTED", f"scanned class changed: {entry}"

    # (b) A plain attendance session with one verified check-in.
    class_id = seed_class(timedelta(seconds=-5), timedelta(minutes=50), "TT2-CAPTURED")
    status, payload = api(
        "POST", "/session/start", token=AUTH["professor_token"], json={"class_id": class_id},
    )
    assert status == 201, f"/session/start returned {status}: {payload}"
    plain_session_id = payload["session"]["id"]
    with APP.app_context():
        db.session.add(AttendanceCheckIn(
            session_id=plain_session_id, student_id=AUTH["student_id"], onchain_tx_hash="0x" + "0" * 64,
        ))
        db.session.commit()
    payload = end_session(plain_session_id)
    assert payload.get("count") == 1, f"expected one capture in the /end response: {payload}"
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    with APP.app_context():
        captured_status = SessionLog.query.filter_by(class_id=class_id).one().status
    assert captured_status == "CONDUCTED", f"class {class_id} with a capture logged {captured_status}"
    print(
        f"   class {on_time} (workload scan): still one CONDUCTED entry; "
        f"class {class_id} (one check-in): exactly one CONDUCTED SessionLog"
    )


def step_attendance_only_conducted():
    """Attendance route, one check-in, /end -> exactly one CONDUCTED entry."""
    class_id = seed_class(timedelta(seconds=-5), timedelta(minutes=50), "TT2-ATT-CONDUCTED")
    STATE["attendance_class_id"] = class_id
    session_id = start_attendance(class_id)
    STATE["attendance_session_id"] = session_id
    seed_check_in(session_id)
    payload = end_session(session_id)
    assert payload.get("count") == 1, f"expected one capture in the /end response: {payload}"
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = assert_resolved(class_id, "CONDUCTED")
    assert entry.get("late") is False, f"expected late=false for an on-time start: {entry}"
    minutes = entry.get("lateMinutes")
    assert isinstance(minutes, int) and minutes <= APP.config["LATE_AFTER_MINUTES"], (
        f"lateMinutes not populated with an on-time value: {entry}"
    )
    STATE["attendance_tx_hash"] = entry["onchainTxHash"]
    print(
        f"   class {class_id}: status={entry['status']} late={entry['late']} "
        f"lateMinutes={minutes} tx={entry['onchainTxHash']}"
    )


def step_attendance_ended_twice():
    """/end again on the attendance-only session -> still exactly one entry."""
    class_id = STATE["attendance_class_id"]
    end_session(STATE["attendance_session_id"])
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = log_for(class_id)
    assert entry is not None and entry.get("onchainTxHash") == STATE["attendance_tx_hash"], (
        f"the entry changed after a second /end: {entry}"
    )
    print(f"   class {class_id}: still one entry, tx={entry['onchainTxHash']}")


def step_attendance_only_zero():
    """Attendance route, zero check-ins, /end -> CONDUCTED_ZERO_STUDENTS."""
    class_id = seed_class(timedelta(seconds=-5), timedelta(minutes=50), "TT2-ATT-ZERO")
    session_id = start_attendance(class_id)
    payload = end_session(session_id)
    assert payload.get("count") == 0, f"expected zero captures in the /end response: {payload}"
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = assert_resolved(class_id, "CONDUCTED_ZERO_STUDENTS")
    print(f"   class {class_id}: status={entry['status']} tx={entry['onchainTxHash']}")


def step_workload_logged_no_second_entry():
    """A class already logged by the workload scan gets no second entry."""
    class_id = STATE["on_time_class_id"]
    before = log_for(class_id)
    assert before is not None and before.get("status") == "CONDUCTED", f"step 6 entry missing: {before}"
    session_id = start_attendance(class_id)
    seed_check_in(session_id)
    end_session(session_id)
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    after = log_for(class_id)
    assert after is not None and after.get("onchainTxHash") == before["onchainTxHash"], (
        f"the workload-scan entry changed: before={before} after={after}"
    )
    print(f"   class {class_id}: still one CONDUCTED entry, tx={after['onchainTxHash']}")


def step_sweep_resolves_attendance():
    """end_time passed, started session + one check-in, no SessionLog -> CONDUCTED."""
    class_id = seed_ended_with_check_in("TT2-SWEEP-CONDUCTED")
    with APP.app_context():
        selected = {row.id for row in overdue_classes(APP, utcnow())}
    assert class_id not in selected, f"class {class_id} with a started session was selected for UNCONDUCTED"
    flag_unconducted_classes(APP)
    assert log_count(class_id) == 1, f"class {class_id} has {log_count(class_id)} SessionLog rows, expected 1"
    entry = assert_resolved(class_id, "CONDUCTED")
    minutes = entry.get("lateMinutes")
    assert entry.get("late") is False and isinstance(minutes, int) and 1 <= minutes <= 3, (
        f"late fields wrong for a start 2 min after start_time: {entry}"
    )
    print(
        f"   class {class_id}: status={entry['status']} late={entry['late']} "
        f"lateMinutes={minutes} tx={entry['onchainTxHash']}"
    )


def step_term_activation():
    """New term activation creates exactly one blank offering per section (D11)."""
    # ADMIN cannot self-register; use the same DB fixture/login as the grade tests.
    admin = {
        "email": f"ttv2.admin.{RUN_ID}@{APP.config['INSTITUTION_EMAIL_DOMAIN']}",
        "password": PASSWORD, "full_name": "Timetable V2 ADMIN",
    }
    ACCOUNTS["admin"] = admin
    with APP.app_context():
        db.session.add(User(
            role=UserRole.ADMIN, email=admin["email"], password_hash=hash_password(PASSWORD),
            full_name=admin["full_name"], department=None, is_active=True,
        ))
        db.session.commit()
        active = Term.query.filter_by(is_active=True).first()
        STATE["previous_active_term_id"] = active.id if active else None
    status, payload = api("POST", "/auth/login", json={"email": admin["email"], "password": PASSWORD})
    assert status == 200 and payload.get("token"), f"ADMIN login failed: {status}: {payload}"
    assert payload["user"]["role"] == "ADMIN", f"fixture is not ADMIN: {payload}"
    AUTH["admin_token"] = payload["token"]
    own_department = ACCOUNTS["hod"]["department"]
    other_department = "Physics"
    for suffix, department in (("A", own_department), ("B", other_department)):
        for letter in ("A", "B"):
            status, payload = api("POST", "/sections", token=AUTH["admin_token"], json={
                "programme": f"TT2-{suffix}-{RUN_ID}", "batch_year": 2024,
                "letter": letter, "department": department,
            })
            assert status == 201, f"section creation failed: {status}: {payload}"
            resource_id = payload["section"]["id"]
            STATE["master_section_ids"].append(resource_id)
            if suffix == "B":
                STATE["foreign_resources"].append(("sections", resource_id, {"letter": "C"}))
    for path, body, patch in (
        ("courses", {"code": f"T2B-{RUN_ID[:24]}", "title": "Scope Test", "is_lab": False,
                     "department": other_department}, {"title": "Changed"}),
        ("rooms", {"name": f"TT2 Room {RUN_ID}", "type": "LECTURE", "block": "B", "capacity": 30,
                   "department": other_department}, {"capacity": 40}),
    ):
        status, payload = api("POST", f"/{path}", token=AUTH["admin_token"], json=body)
        assert status == 201, f"{path} fixture creation failed: {status}: {payload}"
        resource_id = payload[path[:-1]]["id"]
        STATE["master_course_ids" if path == "courses" else "master_room_ids"].append(resource_id)
        STATE["foreign_resources"].append((path, resource_id, patch))
    status, payload = api("POST", "/admin/terms", token=AUTH["admin_token"], json={
        "name": f"TT2-{RUN_ID}", "start_date": "2026-09-01", "end_date": "2027-01-31",
    })
    assert status == 201, f"term creation failed: {status}: {payload}"
    term_id = payload["term"]["id"]
    STATE["master_term_id"] = term_id
    with APP.app_context():
        section_ids = {row.id for row in Section.query.all()}
        assert TermOffering.query.filter_by(term_id=term_id).count() == 0, "new term is not blank"
    STATE["term_activation_attempted"] = True
    status, payload = api("POST", f"/admin/terms/{term_id}/activate", token=AUTH["admin_token"], json={})
    assert status == 200 and payload["term"]["isActive"] is True, f"activation failed: {status}: {payload}"
    assert payload["offerings_created"] == len(section_ids), f"wrong creation count: {payload}"
    with APP.app_context():
        rows = TermOffering.query.filter_by(term_id=term_id).all()
        assert len(rows) == len(section_ids), "activation did not create one offering per section"
        assert {row.section_id for row in rows} == section_ids, "offering coverage differs from all sections"
        assert [row.id for row in Term.query.filter_by(is_active=True)] == [term_id], "not exactly one active term"
        STATE["offering_ids"] = sorted(row.id for row in rows)
    print(f"   term {term_id}: {len(section_ids)} sections, {len(rows)} blank offerings")


def step_term_activation_twice():
    term_id = STATE["master_term_id"]
    status, payload = api("POST", f"/admin/terms/{term_id}/activate", token=AUTH["admin_token"], json={})
    assert status == 200, f"second activation failed: {status}: {payload}"
    assert payload["offerings_created"] == 0, f"second activation created offerings: {payload}"
    assert payload["deactivated_term_ids"] == [], f"second activation changed another term: {payload}"
    with APP.app_context():
        ids = sorted(row.id for row in TermOffering.query.filter_by(term_id=term_id).all())
    assert ids == STATE["offering_ids"], f"second activation changed offering ids: {ids}"
    status, payload = api("GET", f"/hod/offerings?term_id={term_id}", token=AUTH["hod_token"])
    assert status == 200, f"GET /hod/offerings failed: {status}: {payload}"
    assert all(row["section"]["department"] == ACCOUNTS["hod"]["department"] for row in payload["offerings"]), (
        f"HOD received another department's offering: {payload}"
    )
    own_ids = set(STATE["master_section_ids"][:2])
    assert own_ids <= {row["sectionId"] for row in payload["offerings"]}, "own offerings missing"
    print(f"   term {term_id}: zero new offerings, original ids retained")


def step_hod_department_scope():
    """All master writes are refused against another department, including transfers."""
    other_department = "Physics"
    bodies = {
        "sections": {"programme": f"TT2-REFUSED-{RUN_ID}", "batch_year": 2024, "letter": "A",
                     "department": other_department},
        "courses": {"code": f"T2R-{RUN_ID[:24]}", "title": "Refused", "department": other_department},
        "rooms": {"name": f"TT2 Refused {RUN_ID}", "type": "LAB", "department": other_department},
    }

    def refused(method, path, body=None):
        status, payload = api(method, path, token=AUTH["hod_token"], json=body)
        assert status == 403, f"{method} {path} must be 403: {status}: {payload}"
        assert payload.get("check") == "timetable" and payload.get("error") == "out_of_department", (
            f"{method} {path}: wrong department error: {payload}"
        )

    for path, body in bodies.items():
        model = {"sections": Section, "courses": Course, "rooms": Room}[path]
        with APP.app_context():
            before = model.query.count()
        refused("POST", f"/{path}", body)
        with APP.app_context():
            assert model.query.count() == before, f"refused POST /{path} inserted a row"
    for path, resource_id, patch in STATE["foreign_resources"]:
        model = {"sections": Section, "courses": Course, "rooms": Room}[path]
        with APP.app_context():
            before = db.session.get(model, resource_id).to_dict()
        for method in ("PATCH", "PUT", "DELETE"):
            refused(method, f"/{path}/{resource_id}", patch if method != "DELETE" else None)
        # Changing its department to the caller's must not bypass existing-row scope.
        refused("PATCH", f"/{path}/{resource_id}", {"department": ACCOUNTS["hod"]["department"]})
        with APP.app_context():
            assert db.session.get(model, resource_id).to_dict() == before, "refused writes changed a row"
    own_id = STATE["master_section_ids"][0]
    refused("PATCH", f"/sections/{own_id}", {"department": other_department})
    print("   cross-department create/update/delete and department transfers refused")


def step_period_seed_idempotent():
    with APP.app_context():
        before = [row.to_dict() for row in Period.query.order_by(Period.start_time).all()]
        assert seed_periods() == 0, "first repeated seed created periods"
        assert seed_periods() == 0, "second repeated seed created periods"
        after = [row.to_dict() for row in Period.query.order_by(Period.start_time).all()]
    assert after == before, "period seeding changed existing rows"
    expected = [
        (1, "08:40", "09:30", False), (2, "09:30", "10:20", False),
        (3, "10:20", "11:10", False), (None, "11:10", "11:30", True),
        (4, "11:30", "12:20", False), (5, "12:20", "13:10", False),
        (6, "13:10", "14:00", False),
    ]
    assert [(row["ordinal"], row["startTime"], row["endTime"], row["isBreak"]) for row in after] == expected, (
        f"canonical period grid differs: {after}"
    )
    print("   two repeated seeds created zero rows; seven canonical rows retained")


def step_break_period():
    status, payload = api("GET", "/periods", token=AUTH["student_token"])
    assert status == 200, f"GET /periods failed for STUDENT: {status}: {payload}"
    breaks = [row for row in payload["periods"] if row.get("isBreak") is True]
    assert len(breaks) == 1, f"expected exactly one break: {payload}"
    assert breaks[0]["startTime"] == "11:10" and breaks[0]["endTime"] == "11:30", f"wrong break: {breaks}"
    assert breaks[0]["ordinal"] is None, f"break must not have a teaching ordinal: {breaks}"
    assert [row["ordinal"] for row in payload["periods"] if not row["isBreak"]] == [1, 2, 3, 4, 5, 6], (
        f"teaching ordinals differ: {payload}"
    )
    print("   11:10-11:30 break exists, isBreak=true, ordinal=null")


# --------------------------------------------------------------------------
# Phase 2.3 - real DB/Redis, local requests for log capture and scoped config
# --------------------------------------------------------------------------
def directory_api(method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Do not add an IPFS/network prerequisite to these new off-chain tests.
    with patch("app.services.profile_metadata.pin", return_value=None):
        response = APP.test_client().open(f"/api/v1{path}", method=method, headers=headers, **kwargs)
    return response.status_code, response.get_json()


def directory_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=DIRECTORY_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def import_directory(rows, dry_run=False, token=None):
    return directory_api("POST", "/admin/directory/import", token=token or AUTH["admin_token"],
                         data={"file": (io.BytesIO(directory_csv(rows)), "directory.csv"),
                               "dry_run": "true" if dry_run else "false"})


def logged_start(index, **extra):
    row = STATE["directory_rows"][index]
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    old_delivery, old_level = APP.config["AUTH_CODE_DELIVERY"], APP.logger.level
    APP.config["AUTH_CODE_DELIVERY"] = "log"
    APP.logger.setLevel(logging.WARNING)
    APP.logger.addHandler(handler)
    try:
        status, payload = directory_api("POST", "/auth/register/student/start",
                                        json={"roll_number": row["roll_number"], **extra})
    finally:
        APP.logger.removeHandler(handler)
        APP.logger.setLevel(old_level)
        APP.config["AUTH_CODE_DELIVERY"] = old_delivery
    assert status == 200, f"start failed: {status}: {payload}"
    code_records = [record for record in records if record.msg == "Registration code roll=%s recipient=%s code=%s"]
    assert len(code_records) == 1, f"expected one code delivery, got {len(code_records)}"
    record = code_records[0]
    assert record.levelno == logging.WARNING, "code must be logged at WARNING"
    roll, recipient, code = record.args
    assert roll == row["roll_number"] and recipient == row["university_email"], "code delivered outside directory"
    assert len(code) == 6 and code.isascii() and code.isdigit(), "code is not six digits"
    assert "code" not in payload, "public start response leaked the code"
    with APP.app_context():
        stored = registration_code.redis_client().get(registration_code.code_key(roll))
    assert stored is not None and len(stored) == 64 and stored != code, "Redis must hold only a code hash"
    STATE["directory_codes"][index] = code
    return code


def verify_directory(index, code, **extra):
    return directory_api("POST", "/auth/register/student/verify", json={
        "roll_number": STATE["directory_rows"][index]["roll_number"],
        "code": code, "password": PASSWORD, **extra,
    })


def assert_directory_error(status, payload, expected_status, check, error):
    assert status == expected_status, f"expected {expected_status}, got {status}: {payload}"
    assert payload.get("check") == check and payload.get("error") == error, f"wrong error: {payload}"


def step_directory_import():
    domain = APP.config["INSTITUTION_EMAIL_DOMAIN"].strip().lower()
    STATE["directory_rows"] = [{
        "roll_number": f"TT2D-{index}-{RUN_ID}", "full_name": f"Directory Student {index}",
        "university_email": f"ttv2.directory.{index}.{RUN_ID}@{domain}",
        "programme": f"TT2D-{RUN_ID}", "batch_year": 2023,
        "section_letter": "A" if index < 6 else "B", "department": "Computer Science",
    } for index in range(8)]
    with APP.app_context():
        before_rows, before_sections, before_users = DirectoryEntry.query.count(), Section.query.count(), User.query.count()
    status, dry = import_directory(STATE["directory_rows"], dry_run=True)
    assert status == 200 and dry["dry_run"] is True, f"dry run failed: {status}: {dry}"
    assert dry["rows_created"] == 8 and dry["sections_created"] == 2 and not dry["rows_rejected"], f"wrong plan: {dry}"
    with APP.app_context():
        assert DirectoryEntry.query.count() == before_rows and Section.query.count() == before_sections, "dry run wrote rows"
    status, payload = import_directory(STATE["directory_rows"])
    assert status == 200, f"import failed: {status}: {payload}"
    assert {k: v for k, v in payload.items() if k != "dry_run"} == {k: v for k, v in dry.items() if k != "dry_run"}, "dry/live reports differ"
    with APP.app_context():
        rows = DirectoryEntry.query.filter(DirectoryEntry.roll_number.in_([r["roll_number"] for r in STATE["directory_rows"]])).all()
        sections = Section.query.filter_by(programme=f"TT2D-{RUN_ID}").all()
        STATE["directory_ids"] = sorted(row.id for row in rows)
        STATE["directory_section_ids"] = sorted(section.id for section in sections)
        assert len(rows) == 8 and len(sections) == 2, "directory/sections missing"
        assert all(row.claimed_by is None for row in rows), "import claimed a row"
        assert User.query.count() == before_users, "import created accounts"
    status, payload = directory_api("GET", f"/admin/directory?search=TT2D-&page=1&per_page=1", token=AUTH["admin_token"])
    assert status == 200 and len(payload["entries"]) == 1 and payload["total"] >= 8, f"search/paging failed: {payload}"
    status, payload = directory_api("GET", "/admin/directory", token=AUTH["hod_token"])
    assert status == 403, f"HOD accessed ADMIN directory: {payload}"
    status, payload = import_directory(STATE["directory_rows"], token=AUTH["hod_token"])
    assert status == 403, f"HOD imported ADMIN directory: {payload}"


def step_directory_bad_row_atomic():
    first = dict(STATE["directory_rows"][0], roll_number=f"TT2D-BAD-GOOD-{RUN_ID}",
                 university_email=f"ttv2.good.{RUN_ID}@{APP.config['INSTITUTION_EMAIL_DOMAIN']}",
                 programme=f"TT2D-BAD-{RUN_ID}")
    bad = dict(first, roll_number=f"TT2D-BAD-{RUN_ID}", university_email="invalid@example.org", section_letter="AA")
    with APP.app_context():
        before = (DirectoryEntry.query.count(), Section.query.count())
    for dry_run in (True, False):
        status, payload = import_directory([first, bad], dry_run=dry_run)
        assert_directory_error(status, payload, 400, "directory", "invalid_file")
        assert [row["line"] for row in payload["rows_rejected"]] == [3], f"bad line missing: {payload}"
        assert len(payload["rows_rejected"][0]["reasons"]) >= 2, "did not report all row violations"
        with APP.app_context():
            assert (DirectoryEntry.query.count(), Section.query.count()) == before, "invalid file was partially imported"
    # Duplicate rolls are reported on both offending lines.
    status, payload = import_directory([first, dict(first, university_email=STATE["directory_rows"][7]["university_email"])])
    assert status == 400 and {row["line"] for row in payload["rows_rejected"]} == {2, 3}, f"duplicate roll not reported: {payload}"


def step_directory_reimport():
    status, payload = import_directory(STATE["directory_rows"])
    assert status == 200, f"reimport failed: {status}: {payload}"
    assert payload["rows_created"] == payload["rows_updated"] == payload["sections_created"] == 0, f"reimport changed rows: {payload}"
    assert payload["rows_unchanged"] == 8, f"not eight unchanged rows: {payload}"
    with APP.app_context():
        ids = sorted(row.id for row in DirectoryEntry.query.filter(DirectoryEntry.id.in_(STATE["directory_ids"])))
    assert ids == STATE["directory_ids"], "reimport replaced directory rows"


def step_directory_missing_start():
    status, payload = directory_api("POST", "/auth/register/student/start", json={"roll_number": f"TT2D-MISSING-{RUN_ID}"})
    assert_directory_error(status, payload, 404, "directory", "directory_row_missing")


def step_directory_delivery_bound():
    attacker = f"ttv2.attacker.{RUN_ID}@{APP.config['INSTITUTION_EMAIL_DOMAIN']}"
    code = logged_start(0, email=attacker, university_email=attacker)
    other = logged_start(1)
    # Independent six-digit codes may collide; reissue rather than make a flaky fixture.
    for _ in range(3):
        if other != code:
            break
        other = logged_start(1)
    assert other != code, "could not obtain distinct test codes"
    status, payload = verify_directory(1, code)
    assert_directory_error(status, payload, 401, "registration", "code_invalid")
    status, payload = verify_directory(0, code, email=attacker, full_name="Injected", programme="Injected")
    assert status == 201 and payload.get("token"), f"logged code did not register its own roll: {status}: {payload}"
    row = STATE["directory_rows"][0]
    assert payload["user"]["email"] == row["university_email"] and payload["user"]["fullName"] == row["full_name"], "request overrode directory identity"
    assert payload["section"]["programme"] == row["programme"], "request overrode programme"
    with APP.app_context():
        assert registration_code.redis_client().get(registration_code.code_key(row["roll_number"])) is None, "successful code was not consumed"


def step_directory_wrong_code():
    code = logged_start(2)
    wrong = "000000" if code != "000000" else "000001"
    status, payload = verify_directory(2, wrong)
    assert_directory_error(status, payload, 401, "registration", "code_invalid")
    with APP.app_context():
        assert DirectoryEntry.query.filter_by(roll_number=STATE["directory_rows"][2]["roll_number"]).one().claimed_by is None, "wrong code claimed a row"


def step_directory_expired_code():
    old_ttl = APP.config["REGISTRATION_CODE_TTL_SECONDS"]
    try:
        APP.config["REGISTRATION_CODE_TTL_SECONDS"] = 1
        code = logged_start(3)
        time.sleep(1.2)
        status, payload = verify_directory(3, code)
        assert_directory_error(status, payload, 401, "registration", "code_expired")
    finally:
        APP.config["REGISTRATION_CODE_TTL_SECONDS"] = old_ttl


def step_directory_attempt_limit():
    old_maximum = APP.config["REGISTRATION_CODE_MAX_ATTEMPTS"]
    try:
        APP.config["REGISTRATION_CODE_MAX_ATTEMPTS"] = 5
        code = logged_start(4)
        wrong = "000000" if code != "000000" else "000001"
        for _ in range(5):
            status, payload = verify_directory(4, wrong)
            assert_directory_error(status, payload, 401, "registration", "code_invalid")
        status, payload = verify_directory(4, code)
        assert_directory_error(status, payload, 429, "rate_limit", "code_attempts_exceeded")
        code = logged_start(4)
        status, payload = verify_directory(4, code)
        assert_directory_error(status, payload, 429, "rate_limit", "code_attempts_exceeded")
        with APP.app_context():
            ttl = registration_code.redis_client().ttl(registration_code.attempts_key(STATE["directory_rows"][4]["roll_number"]))
        assert 0 < ttl <= 900, f"attempt window missing: {ttl}"
    finally:
        APP.config["REGISTRATION_CODE_MAX_ATTEMPTS"] = old_maximum


def step_directory_section_success():
    code = STATE["directory_codes"][1]
    status, payload = verify_directory(1, code)
    assert status == 201 and payload.get("token") and payload["expiresIn"] == 8 * 3600, f"registration failed: {status}: {payload}"
    STATE["directory_success"] = payload
    row = STATE["directory_rows"][1]
    user_id = payload["user"]["id"]
    with APP.app_context():
        entry = DirectoryEntry.query.filter_by(roll_number=row["roll_number"]).one()
        profile = StudentProfile.query.filter_by(user_id=user_id).one()
        membership = db.session.get(StudentSection, user_id)
        section = Section.query.filter_by(programme=row["programme"], batch_year=2023, letter="A").one()
        assert entry.claimed_by == user_id and membership.section_id == section.id, "claim/membership mismatched"
        assert StudentSection.query.filter_by(user_id=user_id).count() == 1 and membership.since is not None, "membership is not unique or lacks since"
        assert profile.batch == "2023" and profile.programme == row["programme"], "directory batch/programme not copied"
        expected = {"id": section.id, "programme": row["programme"], "batchYear": 2023, "letter": "A"}
    assert payload["section"] == expected, f"wrong verify section: {payload}"
    status, me = directory_api("GET", "/auth/me", token=payload["token"])
    assert status == 200 and me["section"] == expected, f"me section missing: {status}: {me}"
    status, login = directory_api("POST", "/auth/login", json={"email": row["university_email"], "password": PASSWORD})
    assert status == 200 and login["section"] == expected, f"login section missing: {status}: {login}"
    # Re-import may change directory assignment, never permanent membership.
    changed = dict(row, section_letter="B")
    status, report = import_directory([changed])
    assert status == 200 and report["rows_updated"] == 1, f"claimed-row upsert failed: {report}"
    status, me = directory_api("GET", "/auth/me", token=payload["token"])
    assert status == 200 and me["section"] == expected, "re-import moved permanent membership"


def step_directory_claim_twice():
    status, payload = verify_directory(1, STATE["directory_codes"][1])
    assert_directory_error(status, payload, 409, "directory", "directory_already_claimed")
    status, payload = directory_api("POST", "/auth/register/student/start", json={"roll_number": STATE["directory_rows"][1]["roll_number"]})
    assert_directory_error(status, payload, 409, "directory", "directory_already_claimed")


def step_directory_dev_guard():
    old_flag = APP.config["ALLOW_DEV_CODE_ENDPOINT"]
    path = f"/auth/register/student/dev-code/{STATE['directory_rows'][7]['roll_number']}"
    try:
        APP.config["ALLOW_DEV_CODE_ENDPOINT"] = False
        status, payload = directory_api("GET", path)
        assert_directory_error(status, payload, 404, "auth", "dev_code_endpoint_disabled")
        APP.config["ALLOW_DEV_CODE_ENDPOINT"] = True
        code = logged_start(7)
        status, payload = directory_api("GET", path)
        assert status == 200 and payload["code"] == code, f"development helper returned wrong code: {payload}"
    finally:
        APP.config["ALLOW_DEV_CODE_ENDPOINT"] = old_flag


# --------------------------------------------------------------------------
# Cleanup - only this run's rows
# --------------------------------------------------------------------------
def cleanup() -> str | None:
    """Delete this run's rows, children first; returns an error string or None."""
    if APP is None or not ACCOUNTS:
        return None
    try:
        with APP.app_context():
            emails = [account["email"] for account in ACCOUNTS.values()]
            emails += [row["university_email"] for row in STATE["directory_rows"]]
            user_ids = [user.id for user in User.query.filter(User.email.in_(emails)).all()]
            class_ids = set(STATE["class_ids"])
            if user_ids:
                class_ids |= {row.id for row in ScheduledClass.query.filter(
                    ScheduledClass.professor_id.in_(user_ids),
                ).all()}
            class_ids = list(class_ids)
            # Pattern children precede both their faculty and their offerings.
            owned_terms = [STATE["master_term_id"], *STATE["pattern_copy_term_ids"]]
            owned_offerings = db.session.query(TermOffering.id).filter(db.or_(
                TermOffering.term_id.in_([value for value in owned_terms if value is not None]),
                TermOffering.section_id.in_(STATE["master_section_ids"]),
            ))
            TimetableSlot.query.filter(TimetableSlot.offering_id.in_(owned_offerings)).delete(synchronize_session=False)
            session_names = [str(class_id) for class_id in class_ids]
            session_names += [f"workload-{class_id}" for class_id in class_ids]
            for model in (SessionLog, SubstituteToken, WorkloadSession):
                model.query.filter(model.class_id.in_(class_ids)).delete(synchronize_session=False)
            if "student_id" in AUTH:  # the step 13 check-in (this run's unique student)
                AttendanceCheckIn.query.filter_by(student_id=AUTH["student_id"]).delete(
                    synchronize_session=False,
                )
            # Cold resets keep old AttendanceSession rows: only this run's.
            AttendanceSession.query.filter(
                AttendanceSession.class_id.in_(session_names),
                AttendanceSession.start_time >= RUN_STARTED,
            ).delete(synchronize_session=False)
            if STATE["fixture_session_id"] is not None:
                AttendanceSession.query.filter_by(id=STATE["fixture_session_id"]).delete(
                    synchronize_session=False,
                )
            if STATE["seeded_session_ids"]:
                AttendanceSession.query.filter(
                    AttendanceSession.id.in_(STATE["seeded_session_ids"]),
                ).delete(synchronize_session=False)
            ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).delete(synchronize_session=False)
            if STATE["student_row_owned"]:
                Student.query.filter_by(id=AUTH["student_id"]).delete(synchronize_session=False)
            DirectoryEntry.query.filter(DirectoryEntry.roll_number.in_(
                [row["roll_number"] for row in STATE["directory_rows"]],
            )).delete(synchronize_session=False)
            StudentSection.query.filter(StudentSection.user_id.in_(user_ids)).delete(synchronize_session=False)
            for model in (FacultyProfile, StudentProfile):
                model.query.filter(model.user_id.in_(user_ids)).delete(synchronize_session=False)
            User.query.filter(User.email.in_(emails)).delete(synchronize_session=False)
            hod = ACCOUNTS["hod"]
            InstitutionWhitelist.query.filter_by(
                employee_id=hod["employee_id"], email=hod["email"],
            ).delete(synchronize_session=False)
            # Activation creates offerings for unrelated sections in this run's
            # term too. Delete by our term OR our section ids, children first.
            term_id = STATE["master_term_id"]
            TermOffering.query.filter(db.or_(
                TermOffering.term_id.in_([value for value in owned_terms if value is not None]),
                TermOffering.section_id.in_(STATE["master_section_ids"]),
            )).delete(synchronize_session=False)
            # Query by our unique programme too, covering a failure immediately
            # after import but before the returned ids were captured.
            directory_sections = Section.query.filter_by(programme=f"TT2D-{RUN_ID}").all()
            directory_section_ids = [row.id for row in directory_sections]
            TermOffering.query.filter(TermOffering.section_id.in_(directory_section_ids)).delete(synchronize_session=False)
            Section.query.filter(Section.id.in_(directory_section_ids)).delete(synchronize_session=False)
            Section.query.filter(Section.id.in_(STATE["master_section_ids"])).delete(synchronize_session=False)
            Course.query.filter(Course.id.in_(STATE["master_course_ids"])).delete(synchronize_session=False)
            Room.query.filter(Room.id.in_(STATE["master_room_ids"])).delete(synchronize_session=False)
            if STATE["term_activation_attempted"]:
                Term.query.filter_by(id=term_id).update({Term.is_active: False}, synchronize_session=False)
                db.session.flush()
                prior_id = STATE["previous_active_term_id"]
                current = Term.query.filter_by(is_active=True).first()
                if current is not None and current.id != prior_id:
                    raise RuntimeError("another process activated a term during the test; refusing to overwrite it")
                if prior_id is not None and current is None:
                    Term.query.filter_by(id=prior_id).update({Term.is_active: True}, synchronize_session=False)
            Term.query.filter(Term.id.in_(STATE["pattern_copy_term_ids"])).delete(synchronize_session=False)
            Term.query.filter_by(id=term_id).delete(synchronize_session=False)
            db.session.commit()
            if STATE["directory_rows"]:
                client = registration_code.redis_client()
                keys = [key for row in STATE["directory_rows"] for key in (
                    registration_code.code_key(row["roll_number"]),
                    registration_code.attempts_key(row["roll_number"]),
                )]
                client.delete(*keys)
    except Exception as exc:  # a cleanup failure must fail the run, not hide it
        return f"{type(exc).__name__}: {exc}"
    return None


# --------------------------------------------------------------------------
# Phase 2.4 weekly pattern tests (existing steps 0-34 are unchanged)
# --------------------------------------------------------------------------
def pattern_api(method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token or AUTH['hod_token']}"}
    response = APP.test_client().open(f"/api/v1{path}", method=method, headers=headers, **kwargs)
    return response.status_code, response.get_json()


def pattern_row(section=None, course=0, faculty="professor", room=0, day="MON", period="1"):
    return dict(section=str(section or STATE["master_section_ids"][0]),
                course_code=STATE["pattern_courses"][course],
                faculty_employee_id=ACCOUNTS[faculty]["employee_id"],
                room=STATE["pattern_rooms"][room], day=day, period=str(period))


def pattern_import(rows, *, dry_run=False, token=None, columns=None):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns or (
        "section", "course_code", "faculty_employee_id", "room", "day", "period"))
    writer.writeheader()
    writer.writerows(rows)
    return pattern_api("POST", "/hod/timetable/import", token=token, data={
        "file": (io.BytesIO(output.getvalue().encode("utf-8")), "pattern.csv"),
        "term_id": str(STATE["master_term_id"]), "dry_run": "true" if dry_run else "false",
    })


def pattern_snapshot(term_id=None):
    with APP.app_context():
        rows = TimetableSlot.query.join(TermOffering).filter(
            TermOffering.term_id == (term_id or STATE["master_term_id"]),
        ).order_by(TimetableSlot.id).all()
        return [(row.id, row.offering_id, row.course_id, row.faculty_id, row.room_id,
                 row.day_of_week, row.period_id, row.session_group_id) for row in rows]


def pattern_rejected(rows, code, status=409, **kwargs):
    before = pattern_snapshot()
    actual, payload = pattern_import(rows, **kwargs)
    assert_directory_error(actual, payload, status, "timetable", code)
    assert pattern_snapshot() == before, f"{code} partially changed the term pattern"
    return payload


def step_pattern_import():
    department = ACCOUNTS["hod"]["department"]
    with APP.app_context():
        for suffix, is_lab in (("A", False), ("B", False), ("L", True)):
            code = f"P{suffix}-{RUN_ID[:24]}"
            row = Course(code=code, title=f"Pattern {suffix}", department=department, is_lab=is_lab)
            db.session.add(row)
            db.session.flush()
            STATE["master_course_ids"].append(row.id)
            STATE["pattern_courses"].append(code)
        for suffix, kind in (("1", "LECTURE"), ("2", "LECTURE"), ("L", "LAB")):
            name = f"Pattern {suffix} {RUN_ID}"
            row = Room(name=name, type=kind, department=department)
            db.session.add(row)
            db.session.flush()
            STATE["master_room_ids"].append(row.id)
            STATE["pattern_rooms"].append(name)
        db.session.commit()
        section = db.session.get(Section, STATE["master_section_ids"][0])
        label = section.to_dict()["label"]
    rows = [
        pattern_row(course=2, room=2, period=1), pattern_row(course=2, room=2, period=2),
        pattern_row(period=4, faculty="substitute"),
        pattern_row(day="tue", period=1), pattern_row(day="TUE", period=3, course=1),
        pattern_row(day="WED", period=1), pattern_row(day="WED", period=4, course=2, room=2),
        pattern_row(day="WED", period=5, course=2, room=2),
        pattern_row(day="THU", period=2, course=2), pattern_row(day="THU", period=4, course=1),
        pattern_row(day="FRI", period=1), pattern_row(day="FRI", period=5, course=1),
    ]
    rows[0]["section"] = label
    STATE["pattern_rows"] = rows
    status, payload = pattern_import(rows)
    assert status == 200 and payload["rows_created"] == 12, f"pattern import failed: {status}: {payload}"
    assert payload["rows_updated"] == payload["rows_unchanged"] == 0, f"unexpected report: {payload}"
    assert len(pattern_snapshot()) == 12, "pattern count does not match the import"
    with APP.app_context():
        offering_id = TermOffering.query.filter_by(term_id=STATE["master_term_id"],
                                                   section_id=STATE["master_section_ids"][0]).one().id
    status, view = pattern_api("GET", f"/timetable/slots?offering_id={offering_id}")
    assert status == 200 and view["count"] == 12, f"slots read failed: {status}: {view}"
    pairs = [group for group in view["groups"] if group["sessionGroupId"]]
    assert len(pairs) == 2 and [group["periodOrdinals"] for group in pairs] == [[1, 2], [4, 5]], "lab pairs were not merged"
    assert all(len(group["slots"]) == 2 for group in pairs), "a group must contain exactly two cells"
    assert all(slot["periodOrdinal"] is not None for slot in view["slots"]), "slot references a break"


def step_pattern_reimport():
    before = pattern_snapshot()
    status, payload = pattern_import(STATE["pattern_rows"])
    assert status == 200 and payload["rows_unchanged"] == 12, f"re-import not unchanged: {status}: {payload}"
    assert payload["rows_created"] == payload["rows_updated"] == 0, f"re-import changed rows: {payload}"
    assert pattern_snapshot() == before, "re-import changed IDs/groups"


def step_pattern_teacher_clash():
    other = STATE["master_section_ids"][1]
    payload = pattern_rejected([pattern_row(section=other, course=2, room=1),
                                pattern_row(section=other, day="FRI", period=6)], "teacher_clash")
    assert any(item["line"] == 2 for item in payload["violations"]), "clash lacks line number"
    # Incoming-only conflicts must also be checked in the final term snapshot.
    pattern_rejected([pattern_row(day="FRI", period=6),
                      pattern_row(section=other, day="FRI", period=6, room=1)], "teacher_clash")


def step_pattern_room_clash():
    pattern_rejected([pattern_row(section=STATE["master_section_ids"][1], course=2,
                                  faculty="substitute", room=2)], "room_clash")


def step_pattern_section_warning():
    first = pattern_row(day="FRI", period=6)
    last = pattern_row(day="FRI", period=6, course=1)
    status, payload = pattern_import([first, last])
    assert status == 200 and payload["rows_created"] == 1, f"section warning blocked write: {status}: {payload}"
    assert any(item["error"] == "section_clash" and item["line"] == 3 for item in payload["warnings"]), "section warning missing"
    with APP.app_context():
        period = Period.query.filter_by(ordinal=6).one()
        row = TimetableSlot.query.join(TermOffering).filter(TermOffering.term_id == STATE["master_term_id"],
               TermOffering.section_id == STATE["master_section_ids"][0],
               TimetableSlot.day_of_week == 4, TimetableSlot.period_id == period.id).one()
        assert row.course.code == last["course_code"], "last CSV row did not win the unique cell"


def step_pattern_lab_warning():
    status, payload = pattern_import([pattern_row(day="TUE", period=6, course=2)])
    assert status == 200 and payload["rows_created"] == 1, f"lab warning blocked write: {status}: {payload}"
    assert any(item["error"] == "lab_room_mismatch" and item["line"] == 2 for item in payload["warnings"]), "lab warning missing"
    status, payload = pattern_import([pattern_row(day="TUE", period=3, course=2, room=2),
                                      pattern_row(day="TUE", period=4, course=2, room=2)])
    assert status == 200, f"lab across break import failed: {status}: {payload}"
    with APP.app_context():
        rows = TimetableSlot.query.join(TermOffering).join(Period, TimetableSlot.period_id == Period.id).filter(
            TermOffering.term_id == STATE["master_term_id"], TimetableSlot.day_of_week == 1,
            Period.ordinal.in_([3, 4]),
        ).all()
        assert len(rows) == 2 and all(row.session_group_id is None for row in rows), "lab group incorrectly spans the break"


def step_pattern_break():
    pattern_rejected([pattern_row(day="THU", period="BREAK")], "break_period", 400)
    # The PostgreSQL trigger also protects writes which bypass the importer.
    before = pattern_snapshot()
    with APP.app_context():
        if db.engine.dialect.name == "postgresql":
            slot = TimetableSlot.query.join(TermOffering).filter(TermOffering.term_id == STATE["master_term_id"]).first()
            break_id = Period.query.filter_by(is_break=True).one().id
            slot.period_id = break_id
            try:
                db.session.flush()
            except IntegrityError as exc:
                assert getattr(exc.orig.diag, "constraint_name", None) == "ck_timetable_slots_teaching_period", str(exc)
            else:
                raise AssertionError("database accepted a break slot")
            finally:
                db.session.rollback()
    assert pattern_snapshot() == before, "break guard changed the pattern"


def step_pattern_legacy():
    for field in ("start_time", "end_time"):
        row = dict(pattern_row(), **{field: "08:40"})
        payload = pattern_rejected([row], "legacy_format", 400, columns=list(row))
        assert all(column in payload["message"] for column in (
            "section", "course_code", "faculty_employee_id", "room", "day", "period")), "legacy message omits new columns"


def step_pattern_dry_run():
    before = pattern_snapshot()
    status, payload = pattern_import([pattern_row(day="THU", period=6)], dry_run=True)
    assert status == 200 and payload["rows_created"] == 1 and payload["dry_run"], f"dry run report wrong: {status}: {payload}"
    assert payload["proposed_changes"] and not payload["applied"], "dry run lacks a proposed plan"
    assert pattern_snapshot() == before, "dry run wrote slots"
    payload = pattern_rejected([pattern_row(section=STATE["master_section_ids"][1], course=2, room=2)],
                               "teacher_clash", dry_run=True)
    assert {item["error"] for item in payload["violations"]} >= {"teacher_clash", "room_clash"}, "dry run omitted a violation"
    for field, code, value in (("course_code", "course_unknown", "MISSING"),
                               ("room", "room_unknown", "MISSING"),
                               ("faculty_employee_id", "faculty_not_faculty", ACCOUNTS["hod"]["employee_id"])):
        pattern_rejected([dict(pattern_row(day="THU", period=6), **{field: value})], code, 400)


def step_pattern_hod_scope():
    pattern_rejected([pattern_row(section=STATE["master_section_ids"][2])], "out_of_department", 403)
    with APP.app_context():
        own_hod = db.session.get(User, AUTH["hod_id"])
        original = own_hod.department
        own_hod.department = "Physics"
        db.session.commit()
    try:
        pattern_rejected([pattern_row()], "out_of_department", 403)
    finally:
        with APP.app_context():
            db.session.get(User, AUTH["hod_id"]).department = original
            db.session.commit()


def step_pattern_copy():
    with APP.app_context():
        term = Term(name=f"Pattern Copy {RUN_ID}", start_date=utcnow().date(), end_date=utcnow().date())
        db.session.add(term)
        db.session.flush()
        STATE["pattern_copy_term_ids"].append(term.id)
        target = TermOffering(term_id=term.id, section_id=STATE["master_section_ids"][0])
        db.session.add(target)
        db.session.commit()
        term_id, target_id = term.id, target.id
    source_count = len(pattern_snapshot())
    path = f"/hod/offerings/{target_id}/copy-from/{STATE['master_term_id']}"
    status, payload = pattern_api("POST", path, json={})
    assert status == 200 and payload["slots_copied"] == source_count, f"copy failed: {status}: {payload}"
    before = pattern_snapshot(term_id)
    assert len(before) == source_count and any(row[-1] for row in before), "copy lost slots/lab groups"
    status, payload = pattern_api("POST", path, json={})
    assert_directory_error(status, payload, 409, "timetable", "offering_not_blank")
    assert pattern_snapshot(term_id) == before, "second copy changed a non-blank offering"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: unique users + backend /health", step_preflight),
    ("Seed classes (too_late, late, on-time, not-ended)", step_seed_classes),
    ("Attendance start after end_time -> too_late", step_too_late_attendance),
    ("Workload start after end_time -> too_late", step_too_late_workload),
    ("Substitute redeem after end_time -> too_late", step_too_late_redeem),
    ("Start 15 min late -> late=true, lateMinutes 14-16", step_late_start),
    ("On-time start -> late=false", step_on_time_start),
    ("end_time not passed -> not auto-flagged", step_not_auto_flagged),
    ("end_time passed, no session -> UNCONDUCTED on-chain + SessionLog", step_ended_no_session_flagged),
    ("end_time passed, session started -> zero-student, not UNCONDUCTED", step_ended_with_session_not_flagged),
    ("end_time=None -> legacy threshold still flags", step_legacy_null_end_time_flagged),
    ("Start 15 min late, end with zero captures -> zero-student, late", step_late_ended_empty),
    ("End the same session again -> no second entry", step_ended_twice),
    ("End sessions with a capture -> no zero-student entry", step_capture_unaffected),
    ("Attendance only, one check-in, end -> one CONDUCTED entry", step_attendance_only_conducted),
    ("Attendance only, end again -> still one entry", step_attendance_ended_twice),
    ("Attendance only, zero check-ins, end -> zero-student", step_attendance_only_zero),
    ("Workload-scan class, check-in + end -> no second entry", step_workload_logged_no_second_entry),
    ("Sweep: ended, started, one check-in -> CONDUCTED", step_sweep_resolves_attendance),
    ("Term activation -> one blank offering per section", step_term_activation),
    ("Second activation -> zero new offerings", step_term_activation_twice),
    ("HOD writes against another department -> 403", step_hod_department_scope),
    ("Period seeding is idempotent; exact grid", step_period_seed_idempotent),
    ("Break period exists and is flagged", step_break_period),
    ("Directory import creates sections/rows; dry run and ADMIN guards", step_directory_import),
    ("Bad directory row rejects the whole file with line numbers", step_directory_bad_row_atomic),
    ("Directory re-import is idempotent", step_directory_reimport),
    ("Missing directory roll start -> 404", step_directory_missing_start),
    ("Logged code only to directory email; cross-roll code refused", step_directory_delivery_bound),
    ("Wrong registration code -> 401", step_directory_wrong_code),
    ("Expired registration code -> 401 (scoped 1s TTL)", step_directory_expired_code),
    ("Sixth attempt -> 429; resend does not reset attempts", step_directory_attempt_limit),
    ("Registration attaches permanent section; login and me return it", step_directory_section_success),
    ("Same directory roll claimed twice -> 409", step_directory_claim_twice),
    ("Development code endpoint requires explicit opt-in", step_directory_dev_guard),
    ("Pattern import -> 12 slots, merged labs and grouped read", step_pattern_import),
    ("Pattern re-import -> unchanged=all, stable IDs/groups", step_pattern_reimport),
    ("Whole-term teacher clashes -> 409, atomic", step_pattern_teacher_clash),
    ("Whole-term room clash -> 409, atomic", step_pattern_room_clash),
    ("Section clash warns and last row wins", step_pattern_section_warning),
    ("Lab in lecture room warns and writes", step_pattern_lab_warning),
    ("Break period -> 400 and database guard", step_pattern_break),
    ("Legacy time columns -> 400 with new columns", step_pattern_legacy),
    ("Dry run reports every violation without writing", step_pattern_dry_run),
    ("Pattern import obeys authenticated HOD department", step_pattern_hod_scope),
    ("Copy pattern into blank offering; non-blank refused", step_pattern_copy),
]


def run_steps() -> int:
    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            print("      start it first:  .\\venv\\Scripts\\python.exe run.py   (from backend/)")
        except AssertionError as exc:
            print(f"   \u274c FAIL: {exc}")
        except Exception as exc:
            print(f"   \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
        else:
            print("   \u2705 PASS")
            continue
        print("\n" + "=" * 60)
        print(f"   TEST RUN FAILED AT STEP {index}: {title}")
        print("=" * 60)
        return 1
    return 0


def main() -> int:
    print("=" * 60)
    print("   TIMETABLE V2 WORKLOAD + MASTER-DATA TESTS (test_timetable_v2.py)")
    print("=" * 60)
    try:
        result = run_steps()
    finally:
        error = cleanup()
    if error:
        print(f"\n   \u274c FAIL: cleanup: {error}")
        return 1
    print("\n   cleanup: this run's rows removed")
    if result == 0:
        print("\n" + "=" * 60)
        print(f"   ALL TIMETABLE V2 TESTS PASSED ({len(STEPS)} steps)")
        print("=" * 60)
    return result


if __name__ == "__main__":
    sys.exit(main())
