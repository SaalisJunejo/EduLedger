"""End-to-end test of the Phase 1.7a device-bound biometric credential (D4).

Run it against a live stack (from backend/):

    .\\venv\\Scripts\\python.exe test_device_auth.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - AttendanceLedger deployed   (contracts/: npm run deploy:attendance)
    - Flask backend running       (backend/: python run.py), same backend/.env
      as this script (step 9 reads IDENTITY_CHALLENGE_TTL_SECONDS from it)

Steps:
     0. preflight          - GET /health
     0b. registration gap  - /auth/register/student creates a User and a
                             StudentProfile but NO Student row; register-device
                             -> device/enroll -> challenge -> verify -> token
                             must work on that brand-new account
     1. auth setup         - register + log in a unique HOD, faculty and
                             student; student row, owned class, bound device
     2. enroll secret      - POST /students/device/enroll returns the secret ONCE
     3. enroll again       - 409 device_enrollment/secret_already_enrolled
     4. correct HMAC       - challenge -> HMAC -> 30 s token, method
                             "device-biometric"
     5. scan with it       - that token completes a real check-in (locked on-chain)
     6. wrong HMAC         - 401 identity/bad_response
     7. challenge replay   - the same challenge again -> 401 identity/challenge_used
     8. expired challenge  - waited out its TTL -> 401 identity/challenge_expired
     9. wrong device_id    - 403 identity/device_mismatch
    10. after rebind       - admin rebind clears the secret -> 409 identity/not_enrolled
    11. FACULTY blocked    - 403 on all three STUDENT-only routes

Set IDENTITY_CHALLENGE_TTL_SECONDS=5 in backend/.env (and restart the
backend) to keep step 8 short; with the 30 s default it waits ~32 s.

Cleanup removes this run's whitelist row, users, profiles, student row,
class and session. On-chain history is permanent.
"""

import hashlib
import hmac
import os
import sys
import time
from datetime import timedelta
from uuid import uuid4

import requests

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app
from app.extensions import db
from app.models import (
    AttendanceSession,
    FacultyProfile,
    InstitutionWhitelist,
    ScheduledClass,
    Student,
    StudentProfile,
    User,
)
from app.util import utcnow

# Windows consoles often default to cp1252, which cannot print the marks below.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"
RUN_ID = uuid4().hex[:10]

HOD_EMP_ID = f"EMP-DEV-HOD-{RUN_ID}"
HOD_EMAIL = f"hod.device.{RUN_ID}@gcu.edu.pk"
FACULTY_EMAIL = f"faculty.device.{RUN_ID}@gcu.edu.pk"
STUDENT_EMAIL = f"student.device.{RUN_ID}@gcu.edu.pk"
PASSWORD = "DeviceTestPass123!"
STUDENT_NAME = "Device Biometric Student"
DEVICE_ID = f"device_biometric_{RUN_ID}"
# The registration-only step: no Student row exists for this account.
REG_ONLY_NAME = "Registration Only Student"
REG_ONLY_EMAIL = f"student.regonly.{RUN_ID}@gcu.edu.pk"
REG_ONLY_DEVICE_ID = f"device_regonly_{RUN_ID}"
REBOUND_DEVICE_ID = f"device_replacement_{RUN_ID}"
OTHER_DEVICE_ID = f"device_stranger_{RUN_ID}"
COURSE_CODE = f"TEST-DEVICE-{RUN_ID}"

APP = create_app()
AUTH = {"hod_token": None, "faculty_token": None, "student_token": None}
STATE = {
    "faculty_id": None,
    "student_id": None,
    "registration_only_student_id": None,
    "class_id": None,
    "session_id": None,
    "secret": None,
    "used_challenge": None,
    "verification_token": None,
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


def get_challenge() -> str:
    """A fresh single-use challenge for the authenticated student."""
    status, payload = api("GET", "/students/identity/challenge", token=AUTH["student_token"])
    assert status == 200, f"identity/challenge returned {status}: {payload}"
    challenge = payload.get("challenge")
    assert isinstance(challenge, str) and len(challenge) >= 32, (
        f"challenge is not a long random string: {payload}"
    )
    ttl = payload.get("expires_in")
    assert isinstance(ttl, int) and ttl > 0, f"bad expires_in: {payload}"
    return challenge


def hmac_response(challenge: str, device_id: str, secret: str | None = None) -> str:
    """What the phone computes: hex(HMAC_SHA256(secret, challenge + ":" + device_id))."""
    secret = secret if secret is not None else STATE["secret"]
    message = f"{challenge}:{device_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_device(challenge: str, response: str, device_id: str = DEVICE_ID) -> tuple[int, dict]:
    """POST /students/verify-identity-device with the student token."""
    return api(
        "POST",
        "/students/verify-identity-device",
        token=AUTH["student_token"],
        json={"device_id": device_id, "challenge": challenge, "response": response},
    )


def fresh_nonce() -> str:
    """The session's current live nonce (the QR endpoint rotates lazily)."""
    status, payload = api(
        "GET", f"/session/{STATE['session_id']}/qr", token=AUTH["faculty_token"]
    )
    assert status == 200, f"GET /session/{STATE['session_id']}/qr returned {status}: {payload}"
    return payload["current_nonce"]


def scan(token: str, nonce: str, device_id: str = DEVICE_ID) -> tuple[int, dict]:
    """POST /attendance/scan with a verification token from the device path."""
    return api(
        "POST",
        "/attendance/scan",
        token=AUTH["student_token"],
        json={
            "verification_token": token,
            "device_id": device_id,
            "session_id": STATE["session_id"],
            "nonce": nonce,
        },
    )


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
    print(
        f"   attendance_contract={deps['contracts_configured']['attendance_engine']} "
        f"challenge_ttl={APP.config['IDENTITY_CHALLENGE_TTL_SECONDS']}s"
    )


def step_registration_only_student():
    """A brand-new /auth/register/student account has no Student row yet.

    Phase 1.7c bug: the four device routes answered 404 "student <id> does not
    exist" for such an account. They must create the row on demand instead.
    This step pre-creates nothing and runs the whole flow with its own account.
    """
    status, payload = api(
        "POST",
        "/auth/register/student",
        json={
            "full_name": REG_ONLY_NAME,
            "roll_number": f"ROLL-REGONLY-{RUN_ID}",
            "batch": "2022-2026",
            "department": "Computer Science",
            "email": REG_ONLY_EMAIL,
            "password": PASSWORD,
        },
    )
    assert status == 201, f"student registration returned {status}: {payload}"

    status, login = api(
        "POST", "/auth/login", json={"email": REG_ONLY_EMAIL, "password": PASSWORD}
    )
    assert status == 200, f"registration-only student login failed ({status}): {login}"
    token = login["token"]
    user_id = login["user"]["id"]
    STATE["registration_only_student_id"] = user_id

    with APP.app_context():
        assert db.session.get(Student, user_id) is None, (
            f"registration already created a Student row for user {user_id} - "
            "this step must exercise the missing-row path"
        )

    status, payload = api(
        "POST",
        "/student/register-device",
        token=token,
        json={"device_id": REG_ONLY_DEVICE_ID},
    )
    assert status == 200, f"register-device (no Student row) returned {status}: {payload}"
    assert payload["student"]["id"] == user_id, f"unexpected student row: {payload}"
    assert payload["student"]["name"] == REG_ONLY_NAME, f"name not taken from the user: {payload}"
    assert payload["student"]["device_id"] == REG_ONLY_DEVICE_ID, f"unexpected binding: {payload}"

    status, payload = api(
        "POST",
        "/students/device/enroll",
        token=token,
        json={"device_id": REG_ONLY_DEVICE_ID},
    )
    assert status == 201, f"device/enroll (no Student row) returned {status}: {payload}"
    secret = payload.get("device_secret")
    assert isinstance(secret, str) and secret, f"no device_secret: {payload}"

    status, payload = api("GET", "/students/identity/challenge", token=token)
    assert status == 200, f"identity/challenge (no Student row) returned {status}: {payload}"
    challenge = payload["challenge"]

    status, payload = api(
        "POST",
        "/students/verify-identity-device",
        token=token,
        json={
            "device_id": REG_ONLY_DEVICE_ID,
            "challenge": challenge,
            "response": hmac_response(challenge, REG_ONLY_DEVICE_ID, secret=secret),
        },
    )
    assert status == 200, f"verify-identity-device (no Student row) returned {status}: {payload}"
    assert payload.get("verification_token"), f"no verification_token: {payload}"
    assert payload.get("method") == "device-biometric", f"unexpected method: {payload}"
    print(f"   user_id={user_id}: row created on demand, {payload['method']} token issued")


def step_auth_setup():
    """Unique HOD (whitelisted), faculty with an owned class, student with a device."""
    with APP.app_context():
        db.session.add(
            InstitutionWhitelist(
                employee_id=HOD_EMP_ID,
                department="Computer Science",
                name="Dr. Device HOD",
                email=HOD_EMAIL,
            )
        )
        db.session.commit()

    status, payload = api(
        "POST", "/auth/register/hod",
        json={
            "employee_id": HOD_EMP_ID,
            "full_name": "Dr. Device HOD",
            "department": "Computer Science",
            "email": HOD_EMAIL,
            "password": PASSWORD,
        },
    )
    assert status == 201, f"HOD registration returned {status}: {payload}"
    status, h_res = api("POST", "/auth/login", json={"email": HOD_EMAIL, "password": PASSWORD})
    assert status == 200, f"HOD login failed ({status}): {h_res}"
    AUTH["hod_token"] = h_res["token"]

    status, payload = api(
        "POST", "/auth/register/faculty",
        json={
            "employee_id": f"EMP-DEV-FAC-{RUN_ID}",
            "full_name": "Dr. Device Faculty",
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
            "roll_number": f"ROLL-DEV-{RUN_ID}",
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
        # The attendance Student row (normally created by enroll-face, which
        # this test does not need) and a class the faculty owns.
        db.session.add(Student(id=STATE["student_id"], name=STUDENT_NAME))
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=STATE["faculty_id"],
            room="DEVICE-AUTH-TEST",
            course_code=COURSE_CODE,
            start_time=start,
            end_time=start + timedelta(hours=1),
            day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        STATE["class_id"] = scheduled_class.id

    status, payload = api(
        "POST",
        "/student/register-device",
        token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    assert status == 200, f"register-device returned {status}: {payload}"
    assert payload["student"]["device_id"] == DEVICE_ID, f"unexpected binding: {payload}"
    print(
        f"   faculty_id={STATE['faculty_id']} student_id={STATE['student_id']} "
        f"class_id={STATE['class_id']} device={DEVICE_ID}"
    )


def step_enroll_secret():
    """The secret is minted once and returned in that one response."""
    status, payload = api(
        "POST",
        "/students/device/enroll",
        token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    assert status == 201, f"device/enroll returned {status}: {payload}"
    secret = payload.get("device_secret")
    assert isinstance(secret, str) and len(secret) >= 40, (
        f"device_secret is not a 32-byte base64url string: {payload}"
    )
    assert payload["student"]["has_device_secret"] is True, f"secret was not stored: {payload}"
    assert payload["student"]["device_id"] == DEVICE_ID, f"unexpected device: {payload}"
    STATE["secret"] = secret
    print(f"   secret issued ({len(secret)} chars) algorithm={payload.get('algorithm')}")


def step_enroll_twice_rejected():
    """A second enrollment cannot re-read or replace the secret."""
    status, payload = api(
        "POST",
        "/students/device/enroll",
        token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    assert status == 409, f"second enroll must be 409, got {status}: {payload}"
    assert payload.get("check") == "device_enrollment", f"unexpected check: {payload}"
    assert payload.get("error") == "secret_already_enrolled", f"unexpected error: {payload}"
    assert "device_secret" not in payload, f"the secret leaked on a retry: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_correct_hmac_accepted():
    """The real app flow: challenge -> HMAC with the OS-held secret -> token."""
    challenge = get_challenge()
    status, payload = verify_device(challenge, hmac_response(challenge, DEVICE_ID))
    assert status == 200, f"correct HMAC was rejected: {status} {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    assert payload.get("method") == "device-biometric", f"unexpected method: {payload}"
    assert payload.get("expires_in") == APP.config["VERIFICATION_TOKEN_SECONDS"], (
        f"unexpected token lifetime: {payload}"
    )
    STATE["verification_token"] = token
    STATE["used_challenge"] = challenge
    print(f"   method={payload['method']} expires_in={payload['expires_in']}s")


def step_scan_with_device_token():
    """The device-path token is accepted by the unchanged scan route."""
    status, payload = api(
        "POST",
        "/session/start",
        token=AUTH["faculty_token"],
        json={"class_id": STATE["class_id"]},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    STATE["session_id"] = payload["session"]["id"]

    status, payload = scan(STATE["verification_token"], fresh_nonce())
    if payload.get("check") == "session_nonce":
        print("   note: nonce rotated mid-flight - retrying with a fresh nonce")
        status, payload = scan(STATE["verification_token"], fresh_nonce())
    assert status == 200, f"scan with the device token was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"
    print(f"   session={STATE['session_id']} locked on-chain: tx={tx_hash}")


def step_wrong_hmac_rejected():
    """A phone that never held the secret cannot answer the challenge."""
    challenge = get_challenge()
    forged = hmac_response(challenge, DEVICE_ID, secret="not-the-enrolled-secret")
    status, payload = verify_device(challenge, forged)
    assert status == 401, f"wrong HMAC must be 401, got {status}: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "bad_response", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_challenge_replay_rejected():
    """Challenges are single-use: the one spent in step 4 cannot be reused."""
    challenge = STATE["used_challenge"]
    status, payload = verify_device(challenge, hmac_response(challenge, DEVICE_ID))
    assert status == 401, f"replayed challenge must be 401, got {status}: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "challenge_used", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_expired_challenge_rejected():
    """A challenge answered after its TTL is refused."""
    ttl = int(APP.config["IDENTITY_CHALLENGE_TTL_SECONDS"])
    challenge = get_challenge()
    print(f"   waiting out the {ttl}s challenge TTL (lower IDENTITY_CHALLENGE_TTL_SECONDS to speed this up)")
    time.sleep(ttl + 2)
    status, payload = verify_device(challenge, hmac_response(challenge, DEVICE_ID))
    assert status == 401, f"expired challenge must be 401, got {status}: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "challenge_expired", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_wrong_device_rejected():
    """A correct HMAC computed for another device id is still the wrong device."""
    challenge = get_challenge()
    status, payload = verify_device(
        challenge,
        hmac_response(challenge, OTHER_DEVICE_ID),
        device_id=OTHER_DEVICE_ID,
    )
    assert status == 403, f"wrong device must be 403, got {status}: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "device_mismatch", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_rebind_clears_secret():
    """An admin rebind drops the lost phone's secret - the old one stops working."""
    status, payload = api(
        "POST",
        "/admin/rebind-device",
        token=AUTH["hod_token"],
        json={"student_id": STATE["student_id"], "device_id": REBOUND_DEVICE_ID},
    )
    assert status == 200, f"admin/rebind-device returned {status}: {payload}"
    assert payload.get("device_secret_cleared") is True, f"secret was not cleared: {payload}"
    assert payload["student"]["has_device_secret"] is False, f"secret survived: {payload}"
    assert payload["student"]["device_id"] == REBOUND_DEVICE_ID, f"unexpected binding: {payload}"

    challenge = get_challenge()
    status, payload = verify_device(
        challenge,
        hmac_response(challenge, REBOUND_DEVICE_ID),
        device_id=REBOUND_DEVICE_ID,
    )
    assert status == 409, f"old secret after rebind must be 409, got {status}: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "not_enrolled", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_faculty_forbidden():
    """All three routes are STUDENT-only."""
    attempts = [
        ("POST", "/students/device/enroll", {"json": {"device_id": DEVICE_ID}}),
        ("GET", "/students/identity/challenge", {}),
        (
            "POST",
            "/students/verify-identity-device",
            {"json": {"device_id": DEVICE_ID, "challenge": "x", "response": "y"}},
        ),
    ]
    for method, path, kwargs in attempts:
        status, payload = api(method, path, token=AUTH["faculty_token"], **kwargs)
        assert status == 403, f"{method} {path} must be 403 for FACULTY, got {status}: {payload}"
        assert payload.get("check") == "auth", f"unexpected check for {path}: {payload}"
        assert payload.get("error") == "insufficient_role", (
            f"unexpected error for {path}: {payload}"
        )
    print(f"   FACULTY blocked on all {len(attempts)} routes (auth/insufficient_role)")


# --------------------------------------------------------------------------
# Cleanup - only this run's rows
# --------------------------------------------------------------------------
def cleanup() -> str:
    removed = 0
    with APP.app_context():
        emails = [HOD_EMAIL, FACULTY_EMAIL, STUDENT_EMAIL, REG_ONLY_EMAIL]
        user_ids = [u.id for u in User.query.filter(User.email.in_(emails)).all()]
        class_ids = [c.id for c in ScheduledClass.query.filter_by(course_code=COURSE_CODE).all()]
        # Check-ins cascade with their session.
        removed += AttendanceSession.query.filter(
            AttendanceSession.class_id.in_([str(class_id) for class_id in class_ids]),
        ).delete(synchronize_session=False)
        removed += ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).delete(
            synchronize_session=False,
        )
        for student_id in (STATE["student_id"], STATE["registration_only_student_id"]):
            if student_id is not None:
                removed += Student.query.filter_by(id=student_id).delete(
                    synchronize_session=False,
                )
        for model in (FacultyProfile, StudentProfile):
            removed += model.query.filter(model.user_id.in_(user_ids)).delete(
                synchronize_session=False,
            )
        removed += User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        removed += InstitutionWhitelist.query.filter_by(employee_id=HOD_EMP_ID).delete(
            synchronize_session=False,
        )
        db.session.commit()
        assert User.query.filter(User.email.in_(emails)).count() == 0
        assert ScheduledClass.query.filter_by(course_code=COURSE_CODE).count() == 0
    return f"removed {removed} local rows (on-chain history is permanent)"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    (
        "Registration-only student (no Student row): register-device -> enroll "
        "-> challenge -> verify -> token",
        step_registration_only_student,
    ),
    ("Auth setup: unique HOD + faculty + student, class, bound device", step_auth_setup),
    ("Device enroll -> secret returned once", step_enroll_secret),
    ("Device enroll again -> 409 secret_already_enrolled", step_enroll_twice_rejected),
    ("Correct HMAC -> 30s device-biometric token", step_correct_hmac_accepted),
    ("Device token completes a scan (locked on-chain)", step_scan_with_device_token),
    ("Wrong HMAC -> 401 bad_response", step_wrong_hmac_rejected),
    ("Replayed challenge -> 401 challenge_used", step_challenge_replay_rejected),
    ("Expired challenge -> 401 challenge_expired", step_expired_challenge_rejected),
    ("Wrong device_id -> 403 device_mismatch", step_wrong_device_rejected),
    ("Admin rebind clears the secret -> 409 not_enrolled", step_rebind_clears_secret),
    ("FACULTY on all three routes -> 403", step_faculty_forbidden),
]


def main() -> int:
    print("=" * 60)
    print("   DEVICE-BOUND BIOMETRIC TESTS (test_device_auth.py)")
    print(f"   Target: {BASE_URL}  |  Run ID: {RUN_ID}")
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
        print(f"   ALL DEVICE BIOMETRIC TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0 if failed is None and cleanup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
