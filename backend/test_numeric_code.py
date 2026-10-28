"""End-to-end test of the Phase 1.1 rolling numeric attendance code.

Run it against a live stack (from backend/):

    .\\venv\\Scripts\\python.exe test_numeric_code.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - AttendanceLedger deployed   (contracts/: npm run deploy:attendance)
    - Flask backend running       (backend/: python run.py), same backend/.env
      as this script (the expected code is derived with its SECRET_KEY)

Steps:
     0. preflight          - GET /health
     1. auth setup         - register + log in a unique faculty and student;
                             create a ScheduledClass owned by the faculty
     2. enroll-face        - demo enrollment for the student
     3. register-device    - bind this run's device to the student
     4. session/start      - faculty opens the session; reads rotation_seconds
     5. code stable        - numeric_code is 6 digits, matches the HMAC of the
                             current nonce and is identical inside one window
     6. code rotates       - after rotation_seconds the nonce and code change
     7. scan (valid code)  - session_id + numeric_code -> locked on-chain
     8. scan (again)       - same student, valid code -> 409 onchain_lock
     9. scan (wrong code)  - 400 session_nonce/code_mismatch
    10. brute force        - the 6th wrong code -> 429 rate_limit/too_many_attempts
    11. student QR fetch   - STUDENT token on /session/<id>/qr -> 403
    Phase 1.1b - numeric_code WITHOUT session_id (the server finds the session):
    12. second session     - a second owned class + live session
    13. code-only scan     - numeric_code only -> locked on-chain in session 2
    14. checked_in_at      - the success response carries the mirrored check-in
                             time: ISO 8601 UTC, within 15 s of now
    15. code-only again    - same student, valid code -> 409 onchain_lock
    16. code-only wrong    - 400 session_nonce/code_mismatch
    17. code-only brute    - the 6th wrong code-only attempt -> 429 (counted
                             per student, separate from step 10's counter)
    Cleanup removes this run's users, profiles, student row, classes and sessions.
    On-chain history is permanent; the server's attempt counters are keyed by
    this run's unique student (and session), so they never leak.
"""

import base64
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests
from PIL import Image

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app
from app.extensions import db
from app.models import (
    AttendanceCheckIn,
    AttendanceSession,
    FacultyProfile,
    ScheduledClass,
    Student,
    StudentProfile,
    User,
)
from app.services import blockchain
from app.services.attendance_code import (
    MAX_WRONG_CODE_ATTEMPTS,
    MAX_WRONG_CODE_ONLY_ATTEMPTS,
    derive_code,
)
from app.util import iso_z, utcnow

# Windows consoles often default to cp1252, which cannot print the marks below.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"
RUN_ID = uuid4().hex[:10]
FACULTY_EMAIL = f"faculty.code.{RUN_ID}@gcu.edu.pk"
STUDENT_EMAIL = f"student.code.{RUN_ID}@gcu.edu.pk"
PASSWORD = "CodeTestPass123!"
STUDENT_NAME = "Numeric Code Student"
DEVICE_ID = f"device_numeric_code_{RUN_ID}"
COURSE_CODE = f"TEST-CODE-{RUN_ID}"

APP = create_app()
AUTH = {"faculty_token": None, "student_token": None}
STATE = {
    "faculty_id": None,
    "student_id": None,
    "student_row_owned": False,
    "class_id": None,
    "session_id": None,
    "rotation_seconds": None,
    "first_nonce": None,
    "first_code": None,
    "code_mismatches": 0,  # every code_mismatch the server has counted for us
    # Phase 1.1b code-only scans (no session_id)
    "code_class_id": None,
    "code_session_id": None,
    "code_only_response": None,
    "code_only_mismatches": 0,  # counted per student, separately from the above
}


# --------------------------------------------------------------------------
# Synthetic "face" images (same approach as test_identity_scan.py)
# --------------------------------------------------------------------------
def _png_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _gradient_image(shift: int = 0) -> Image.Image:
    image = Image.new("L", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            pixels[x, y] = max(0, min(255, (x + shift) * 255 // 95))
    return image


ENROLL_IMAGE = _png_data_uri(_gradient_image(shift=0))
CAPTURE_IMAGE = _png_data_uri(_gradient_image(shift=2))


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


def get_qr(session_id: int | None = None) -> dict:
    """The projector payload, fetched with the faculty token."""
    session_id = session_id or STATE["session_id"]
    status, payload = api("GET", f"/session/{session_id}/qr", token=AUTH["faculty_token"])
    assert status == 200, f"GET /session/{session_id}/qr returned {status}: {payload}"
    return payload


def verification_token() -> str:
    """A freshly minted (30 s) verification token for the student."""
    status, payload = api(
        "POST", "/attendance/verify-identity", token=AUTH["student_token"],
        json={"face_image": CAPTURE_IMAGE},
    )
    assert status == 200, f"verify-identity returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    return token


def scan_code(code: str) -> tuple[int, dict]:
    """POST /attendance/scan with session_id + numeric_code (no nonce)."""
    status, payload = api(
        "POST",
        "/attendance/scan",
        token=AUTH["student_token"],
        json={
            "verification_token": verification_token(),
            "device_id": DEVICE_ID,
            "session_id": STATE["session_id"],
            "numeric_code": code,
        },
    )
    if payload.get("error") == "code_mismatch":
        STATE["code_mismatches"] += 1
    return status, payload


def scan_live_code() -> tuple[int, dict]:
    """Scan with the code shown right now, retrying once if it rotated mid-flight."""
    status, payload = scan_code(get_qr()["numeric_code"])
    if payload.get("error") == "code_mismatch":
        print("   note: code rotated mid-flight - retrying with the fresh code")
        status, payload = scan_code(get_qr()["numeric_code"])
    return status, payload


def wrong_code(session_id: int | None = None) -> str:
    """A well-formed code that differs from the one currently shown."""
    current = int(get_qr(session_id)["numeric_code"])
    return f"{(current + 1) % 10**6:06d}"


def scan_code_only(code: str) -> tuple[int, dict]:
    """POST /attendance/scan with numeric_code only - no session_id, no nonce."""
    status, payload = api(
        "POST",
        "/attendance/scan",
        token=AUTH["student_token"],
        json={
            "verification_token": verification_token(),
            "device_id": DEVICE_ID,
            "numeric_code": code,
        },
    )
    if payload.get("error") == "code_mismatch":
        STATE["code_only_mismatches"] += 1
    return status, payload


def scan_live_code_only() -> tuple[int, dict]:
    """Code-only scan with session 2's current code, retrying once on rotation."""
    status, payload = scan_code_only(get_qr(STATE["code_session_id"])["numeric_code"])
    if payload.get("error") in ("code_mismatch", "ambiguous_code"):
        print(f"   note: {payload['error']} mid-flight - retrying with the fresh code")
        status, payload = scan_code_only(get_qr(STATE["code_session_id"])["numeric_code"])
    return status, payload


def assert_code_shape(payload: dict) -> str:
    code = payload.get("numeric_code")
    assert isinstance(code, str) and len(code) == 6 and code.isdigit(), (
        f"numeric_code is not 6 digits: {payload.get('numeric_code')!r}"
    )
    expected = derive_code(payload["current_nonce"], APP.config["SECRET_KEY"])
    assert code == expected, (
        f"numeric_code {code} is not the HMAC of the current nonce ({expected}); "
        "is the server using the same backend/.env SECRET_KEY?"
    )
    return code


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    print(f"   attendance_contract={deps['contracts_configured']['attendance_engine']}")


def step_auth_setup():
    status, payload = api(
        "POST", "/auth/register/faculty",
        json={
            "employee_id": f"EMP-CODE-{RUN_ID}",
            "full_name": "Dr. Numeric Code",
            "department": "Computer Science",
            "email": FACULTY_EMAIL,
            "password": PASSWORD,
        },
    )
    assert status == 201, f"faculty registration returned {status}: {payload}"
    status, f_res = api("POST", "/auth/login", json={"email": FACULTY_EMAIL, "password": PASSWORD})
    assert status == 200, f"faculty login failed ({status}): {f_res}"
    AUTH["faculty_token"] = f_res["token"]
    STATE["faculty_id"] = f_res["user"]["id"]

    status, payload = api(
        "POST", "/auth/register/student",
        json={
            "full_name": STUDENT_NAME,
            "roll_number": f"ROLL-CODE-{RUN_ID}",
            "batch": "2022-2026",
            "department": "Computer Science",
            "email": STUDENT_EMAIL,
            "password": PASSWORD,
        },
    )
    assert status == 201, f"student registration returned {status}: {payload}"
    status, s_res = api("POST", "/auth/login", json={"email": STUDENT_EMAIL, "password": PASSWORD})
    assert status == 200, f"student login failed ({status}): {s_res}"
    AUTH["student_token"] = s_res["token"]
    STATE["student_id"] = s_res["user"]["id"]

    with APP.app_context():
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=STATE["faculty_id"],
            room="NUMERIC-CODE-TEST",
            course_code=COURSE_CODE,
            start_time=start,
            end_time=start + timedelta(hours=1),
            day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        STATE["class_id"] = scheduled_class.id
    print(
        f"   faculty_id={STATE['faculty_id']} student_id={STATE['student_id']} "
        f"class_id={STATE['class_id']}"
    )


def step_enroll_face():
    status, payload = api(
        "POST", "/student/enroll-face", token=AUTH["student_token"],
        json={"name": STUDENT_NAME, "face_image": ENROLL_IMAGE},
    )
    assert status == 201, f"enroll-face returned {status}: {payload}"
    assert payload["student"]["id"] == STATE["student_id"], f"unexpected student: {payload}"
    STATE["student_row_owned"] = bool(payload.get("created"))
    print(f"   student row created={payload.get('created')}")


def step_register_device():
    status, payload = api(
        "POST", "/student/register-device", token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    assert status == 200, f"register-device returned {status}: {payload}"
    assert payload["student"]["device_id"] == DEVICE_ID, f"unexpected binding: {payload}"


def step_start_session():
    status, payload = api(
        "POST", "/session/start", token=AUTH["faculty_token"],
        json={"class_id": STATE["class_id"]},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    rotation = payload.get("rotation_seconds")
    assert isinstance(rotation, int) and rotation > 0, f"bad rotation_seconds: {payload}"
    STATE["session_id"] = payload["session"]["id"]
    STATE["rotation_seconds"] = rotation
    print(f"   session={STATE['session_id']} rotation={rotation}s")


def step_code_stable():
    first = get_qr()
    code1 = assert_code_shape(first)
    time.sleep(1)
    second = get_qr()
    code2 = assert_code_shape(second)
    assert first["current_nonce"] == second["current_nonce"], (
        f"nonce rotated within 1s of a fresh {STATE['rotation_seconds']}s window"
    )
    assert code1 == code2, f"code changed inside one window: {code1} -> {code2}"
    STATE["first_nonce"] = first["current_nonce"]
    STATE["first_code"] = code1
    print(f"   code={code1} stable across two polls")


def step_code_rotates():
    wait = STATE["rotation_seconds"] + 1
    print(f"   waiting {wait}s for the next rotation window")
    time.sleep(wait)
    after = get_qr()
    code = assert_code_shape(after)
    assert after["current_nonce"] != STATE["first_nonce"], "nonce did not rotate"
    assert code != STATE["first_code"], f"code did not change after rotation: {code}"
    print(f"   code {STATE['first_code']} -> {code}")


def step_valid_code_scan():
    status, payload = scan_live_code()
    assert status == 200, f"valid code scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"
    with APP.app_context():
        assert blockchain.is_locked(STATE["student_id"], STATE["session_id"]), (
            "AttendanceLedger.isLocked() is false after a successful code scan"
        )
    print(f"   locked on-chain: tx={tx_hash} block={payload['block_number']}")


def step_rescan_blocked():
    status, payload = scan_live_code()
    assert status == 409, f"re-scan must be rejected with 409, got {status}: {payload}"
    assert payload.get("check") == "onchain_lock", f"unexpected check: {payload}"
    assert payload.get("error") == "attendance_already_locked", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_wrong_code_blocked():
    status, payload = scan_code(wrong_code())
    assert status == 400, f"wrong code must be rejected with 400, got {status}: {payload}"
    assert payload.get("check") == "session_nonce", f"unexpected check: {payload}"
    assert payload.get("error") == "code_mismatch", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_brute_force_limited():
    while STATE["code_mismatches"] < MAX_WRONG_CODE_ATTEMPTS:
        attempt = STATE["code_mismatches"] + 1
        status, payload = scan_code(wrong_code())
        assert status == 400 and payload.get("error") == "code_mismatch", (
            f"wrong attempt #{attempt} must be 400 code_mismatch, got {status}: {payload}"
        )
    status, payload = scan_code(wrong_code())
    assert status == 429, (
        f"attempt #{MAX_WRONG_CODE_ATTEMPTS + 1} must be rejected with 429, got {status}: {payload}"
    )
    assert payload.get("check") == "rate_limit", f"unexpected check: {payload}"
    assert payload.get("error") == "too_many_attempts", f"unexpected error: {payload}"
    print(
        f"   {MAX_WRONG_CODE_ATTEMPTS} wrong codes -> attempt #{MAX_WRONG_CODE_ATTEMPTS + 1} "
        f"rejected 429: {payload['message']}"
    )


def step_student_qr_forbidden():
    status, payload = api("GET", f"/session/{STATE['session_id']}/qr", token=AUTH["student_token"])
    assert status == 403, f"student QR fetch must be 403, got {status}: {payload}"
    assert "numeric_code" not in payload and "current_nonce" not in payload, (
        f"403 response leaked the code or nonce: {payload}"
    )
    print(f"   rejected 403: {payload.get('message') or payload.get('error')}")


# --------------------------------------------------------------------------
# Phase 1.1b - code-only scans
# --------------------------------------------------------------------------
def step_start_code_only_session():
    with APP.app_context():
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=STATE["faculty_id"],
            room="NUMERIC-CODE-TEST-2",
            course_code=COURSE_CODE,  # same code, so cleanup() finds it
            start_time=start,
            end_time=start + timedelta(hours=1),
            day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        STATE["code_class_id"] = scheduled_class.id
    status, payload = api(
        "POST", "/session/start", token=AUTH["faculty_token"],
        json={"class_id": STATE["code_class_id"]},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    STATE["code_session_id"] = payload["session"]["id"]
    print(f"   class_id={STATE['code_class_id']} session={STATE['code_session_id']}")


def step_code_only_scan():
    status, payload = scan_live_code_only()
    assert status == 200, f"valid code-only scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    assert payload.get("session_id") == STATE["code_session_id"], (
        f"code resolved to session {payload.get('session_id')}, expected {STATE['code_session_id']}"
    )
    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"
    with APP.app_context():
        assert blockchain.is_locked(STATE["student_id"], STATE["code_session_id"]), (
            "AttendanceLedger.isLocked() is false after a successful code-only scan"
        )
    STATE["code_only_response"] = payload
    print(f"   locked on-chain: session={payload['session_id']} tx={tx_hash}")


def step_checked_in_at():
    payload = STATE["code_only_response"]
    assert payload is not None, "no successful scan response to inspect"
    raw = payload.get("checked_in_at")
    assert isinstance(raw, str) and raw, f"checked_in_at missing from the scan response: {payload}"
    parsed = datetime.fromisoformat(raw)
    assert parsed.utcoffset() == timedelta(0), f"checked_in_at is not UTC: {raw}"
    drift = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert drift <= 15, f"checked_in_at {raw} is {drift:.1f}s away from now"
    with APP.app_context():
        mirrored = AttendanceCheckIn.query.filter_by(
            session_id=STATE["code_session_id"], student_id=STATE["student_id"]
        ).first()
        assert mirrored is not None, "no AttendanceCheckIn mirror row for the code-only scan"
        assert iso_z(mirrored.checked_in_at) == raw, (
            f"checked_in_at {raw} differs from the mirror's {iso_z(mirrored.checked_in_at)}"
        )
    print(f"   checked_in_at={raw} ({drift:.1f}s from now, matches the mirror)")


def step_code_only_rescan_blocked():
    status, payload = scan_live_code_only()
    assert status == 409, f"code-only re-scan must be rejected with 409, got {status}: {payload}"
    assert payload.get("check") == "onchain_lock", f"unexpected check: {payload}"
    assert payload.get("error") == "attendance_already_locked", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_code_only_wrong():
    status, payload = scan_code_only(wrong_code(STATE["code_session_id"]))
    assert status == 400, f"wrong code-only scan must be rejected with 400, got {status}: {payload}"
    assert payload.get("check") == "session_nonce", f"unexpected check: {payload}"
    assert payload.get("error") == "code_mismatch", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_code_only_brute_force_limited():
    while STATE["code_only_mismatches"] < MAX_WRONG_CODE_ONLY_ATTEMPTS:
        attempt = STATE["code_only_mismatches"] + 1
        status, payload = scan_code_only(wrong_code(STATE["code_session_id"]))
        assert status == 400 and payload.get("error") == "code_mismatch", (
            f"wrong code-only attempt #{attempt} must be 400 code_mismatch, got {status}: {payload}"
        )
    status, payload = scan_code_only(wrong_code(STATE["code_session_id"]))
    assert status == 429, (
        f"code-only attempt #{MAX_WRONG_CODE_ONLY_ATTEMPTS + 1} must be rejected with 429, "
        f"got {status}: {payload}"
    )
    assert payload.get("check") == "rate_limit", f"unexpected check: {payload}"
    assert payload.get("error") == "too_many_attempts", f"unexpected error: {payload}"
    print(
        f"   {MAX_WRONG_CODE_ONLY_ATTEMPTS} wrong code-only scans -> attempt "
        f"#{MAX_WRONG_CODE_ONLY_ATTEMPTS + 1} rejected 429: {payload['message']}"
    )


# --------------------------------------------------------------------------
# Cleanup - only this run's rows
# --------------------------------------------------------------------------
def cleanup() -> str:
    removed = 0
    with APP.app_context():
        user_ids = [u.id for u in User.query.filter(User.email.in_([FACULTY_EMAIL, STUDENT_EMAIL])).all()]
        class_ids = [c.id for c in ScheduledClass.query.filter_by(course_code=COURSE_CODE).all()]
        removed += AttendanceSession.query.filter(
            AttendanceSession.class_id.in_([str(class_id) for class_id in class_ids]),
        ).delete(synchronize_session=False)
        removed += ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).delete(
            synchronize_session=False,
        )
        if STATE["student_row_owned"] and STATE["student_id"] is not None:
            removed += Student.query.filter_by(id=STATE["student_id"]).delete(synchronize_session=False)
        for model in (FacultyProfile, StudentProfile):
            removed += model.query.filter(model.user_id.in_(user_ids)).delete(synchronize_session=False)
        removed += User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.session.commit()
        assert User.query.filter(User.email.in_([FACULTY_EMAIL, STUDENT_EMAIL])).count() == 0
        assert ScheduledClass.query.filter_by(course_code=COURSE_CODE).count() == 0
    return f"removed {removed} local rows (on-chain history is permanent)"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Auth setup: unique faculty + student, owned class", step_auth_setup),
    ("Enroll student face (demo enrollment)", step_enroll_face),
    ("Register device", step_register_device),
    ("Start attendance session (faculty JWT)", step_start_session),
    ("Numeric code is 6 digits and stable inside one window", step_code_stable),
    ("Numeric code changes after rotation", step_code_rotates),
    ("Scan by valid code -> locked on-chain", step_valid_code_scan),
    ("Same student scans again -> blocked on-chain", step_rescan_blocked),
    ("Wrong code -> code_mismatch", step_wrong_code_blocked),
    ("6th wrong code -> 429 too_many_attempts", step_brute_force_limited),
    ("Student fetches /session/<id>/qr -> 403", step_student_qr_forbidden),
    ("Second class + live session for code-only scans", step_start_code_only_session),
    ("Code-only scan (no session_id) -> locked on-chain", step_code_only_scan),
    ("Scan response carries checked_in_at (UTC, within 15 s)", step_checked_in_at),
    ("Same student code-only again -> blocked on-chain", step_code_only_rescan_blocked),
    ("Wrong code-only scan -> code_mismatch", step_code_only_wrong),
    ("6th wrong code-only scan -> 429 too_many_attempts", step_code_only_brute_force_limited),
]


def main() -> int:
    print("=" * 60)
    print("   NUMERIC ATTENDANCE CODE TESTS (test_numeric_code.py)")
    print("=" * 60)

    failed = None
    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            failed = (index, title)
            break
        except AssertionError as exc:
            print(f"   \u274c FAIL: {exc}")
            failed = (index, title)
            break
        except Exception as exc:
            print(f"   \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
            failed = (index, title)
            break
        print("   \u2705 PASS")

    print("\nCleanup")
    cleanup_ok = True
    try:
        print(f"   \u2705 PASS: {cleanup()}")
    except Exception as exc:
        cleanup_ok = False
        print(f"   \u274c FAIL: cleanup failed: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    if failed is not None:
        print(f"   TEST RUN FAILED AT STEP {failed[0]}: {failed[1]}")
    elif not cleanup_ok:
        print("   TEST RUN FAILED DURING CLEANUP")
    else:
        print(f"   ALL NUMERIC CODE TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0 if failed is None and cleanup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
