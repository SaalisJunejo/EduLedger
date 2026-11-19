"""End-to-end test of Phase 2.1 late-start handling (PRD v2.2 4.8.3-4.8.6, D18/D19).

Run it against a live stack (from backend/), after restarting Flask so it
serves the Phase 2.1 code:

    .\\venv\\Scripts\\python.exe test_timetable_v2.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - WorkloadLedger deployed     (contracts/: npm run deploy:workload)
    - Flask backend running       (backend/: python run.py)

Steps:
    0. preflight          - register + log in unique professor, substitute,
                            student and HOD accounts; GET /health
    1. seed classes       - four absolute-time ScheduledClass rows (see below)
    2. too_late (attendance) - POST /session/start after end_time      -> 403
    3. too_late (workload)   - POST /faculty/session/start after end_time -> 403
    4. too_late (substitute) - POST /faculty/substitute/redeem after end_time -> 403
    5. late start         - workload start 15 min after start_time, zero-attendance;
                            /faculty/logs -> late=true, lateMinutes 14-16
    6. on-time start      - workload start at start_time, one student scan;
                            /faculty/logs -> late=false
    7. not auto-flagged   - a class whose end_time has not passed is not
                            selected by the auto-flag job (and is at end_time + 1 min)
    8. ended, no session  - flag_unconducted_classes() called directly ->
                            UNCONDUCTED on-chain + local SessionLog
    9. ended, started     - a session was started -> not selected, not flagged
   10. legacy null end    - end_time=None -> threshold rule still selects and
                            flags it (threshold overridden to 1 min in-process)

Notes:
    - SCHEDULER_ENABLED=false is set before importing `app`, so this process
      never hosts a second scheduler. The auto-flag selection is checked
      in-process via app.scheduler.overdue_classes(); steps 7 and 9 anchor
      nothing. Steps 8 and 10 call flag_unconducted_classes() directly, which
      also flags any other overdue row in the shared DB (as the live job would;
      in step 10 with the 1-minute threshold, like test_workload.py). If the
      live job flags the step 8 class first, the outcome asserted is the same.
    - The too_late class already ended, so the live server's auto-flag job
      could log it UNCONDUCTED mid-run (and workload start / redeem would then
      answer class_already_logged instead). It is seeded with one ended
      attendance session, which D19 counts as "a session was held", so the
      live job never flags it.
    - Cleanup deletes only this run's rows (users by unique email, their
      classes, sessions, tokens and logs). On-chain entries are append-only
      and stay on the local Hardhat chain.
"""

import os
import sys
from datetime import timedelta
from uuid import uuid4

# Must be set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

import requests  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    AttendanceSession, FacultyProfile, InstitutionWhitelist, ScheduledClass, SessionLog,
    Student, StudentProfile, SubstituteToken, User, WorkloadSession,
)
from app.scheduler import flag_unconducted_classes, overdue_classes  # noqa: E402
from app.services import workload  # noqa: E402
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
    "student_row_owned": False,
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
    """POST /faculty/session/start after end_time -> too_late."""
    status, payload = api(
        "POST", "/faculty/session/start", token=AUTH["professor_token"],
        json={"class_id": STATE["too_late_class_id"]},
    )
    assert_too_late(status, payload, "/faculty/session/start")


def step_too_late_redeem():
    """A valid substitute token redeemed after end_time -> too_late."""
    status, payload = api(
        "POST", "/faculty/substitute/issue", token=AUTH["professor_token"],
        json={"class_id": STATE["too_late_class_id"], "substitute_id": AUTH["substitute_id"]},
    )
    assert status == 201, f"/faculty/substitute/issue returned {status}: {payload}"
    status, payload = api(
        "POST", "/faculty/substitute/redeem", token=AUTH["substitute_token"],
        json={"token": payload["substitute_token"]},
    )
    assert_too_late(status, payload, "/faculty/substitute/redeem")
    with APP.app_context():
        assert WorkloadSession.query.filter_by(class_id=STATE["too_late_class_id"]).count() == 0, (
            "a rejected start must not open a workload session"
        )


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
    """end_time passed but a session was started -> not flagged."""
    class_id = seed_class(timedelta(minutes=-60), timedelta(seconds=-30), "TT2-ENDED-STARTED", started=True)
    with APP.app_context():
        selected = {row.id for row in overdue_classes(APP, utcnow())}
    assert class_id not in selected, f"class {class_id} with a started session was selected"
    flag_unconducted_classes(APP)
    with APP.app_context():
        assert SessionLog.query.filter_by(class_id=class_id).count() == 0, (
            f"class {class_id} with a started session got a SessionLog"
        )
        onchain = workload.get_workload_log(class_id)
    assert onchain["logged"] is False, f"class {class_id} with a started session is logged on-chain: {onchain}"
    print(f"   class {class_id} (ended, session started): not selected, no SessionLog, not on-chain")


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
            user_ids = [user.id for user in User.query.filter(User.email.in_(emails)).all()]
            class_ids = set(STATE["class_ids"])
            if user_ids:
                class_ids |= {row.id for row in ScheduledClass.query.filter(
                    ScheduledClass.professor_id.in_(user_ids),
                ).all()}
            class_ids = list(class_ids)
            session_names = [str(class_id) for class_id in class_ids]
            session_names += [f"workload-{class_id}" for class_id in class_ids]
            for model in (SessionLog, SubstituteToken, WorkloadSession):
                model.query.filter(model.class_id.in_(class_ids)).delete(synchronize_session=False)
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
            for model in (FacultyProfile, StudentProfile):
                model.query.filter(model.user_id.in_(user_ids)).delete(synchronize_session=False)
            User.query.filter(User.email.in_(emails)).delete(synchronize_session=False)
            hod = ACCOUNTS["hod"]
            InstitutionWhitelist.query.filter_by(
                employee_id=hod["employee_id"], email=hod["email"],
            ).delete(synchronize_session=False)
            db.session.commit()
    except Exception as exc:  # a cleanup failure must fail the run, not hide it
        return f"{type(exc).__name__}: {exc}"
    return None


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
    ("end_time passed, session started -> not flagged", step_ended_with_session_not_flagged),
    ("end_time=None -> legacy threshold still flags", step_legacy_null_end_time_flagged),
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
    print("   TIMETABLE V2 LATE-START TESTS (test_timetable_v2.py)")
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
