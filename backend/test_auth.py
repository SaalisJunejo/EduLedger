"""End-to-end integration tests for authentication and role governance (Stage 0).

Tests the full auth lifecycle against the live running server:
- HOD registration: whitelist rejection, temporary whitelist acceptance, duplicate email rejection.
- Faculty registration: invalid domain rejection, valid registration, duplicate checks.
- Student registration: invalid domain rejection, valid registration, duplicate checks.
- Login: valid credentials for HOD, Faculty, Student; wrong password and unknown email rejection.
- /auth/me: missing token, malformed token, expired token, valid profile returns for all roles.
- require_auth(roles=[...]): blocking wrong-role tokens with 403 and allowing matching role.
- Profile metadata (Phase 1.6, D7): registrations return an IPFS CID, /auth/me repeats it,
  and the pinned document carries the name but never the email.

Every run uses unique IDs and emails (random suffix). Cleanup removes only rows
created during the run and preserves pre-seeded demo whitelist entries.

Run against a live stack (from backend/):
    .\\venv\\Scripts\\python.exe test_auth.py

The profile-metadata steps also need the local IPFS daemon (`ipfs daemon`),
since they read the pinned document back through the Kubo RPC.
"""

import os
import re
import secrets
import sys
import time

import jwt
import requests
from flask import Flask, g, jsonify

# Windows consoles often default to cp1252; force UTF-8 so status marks print safely.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

# Disable scheduler in this script's local AppContext.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app import create_app
from app.extensions import db
from app.models import FacultyProfile, InstitutionWhitelist, StudentProfile, User
from app.services.auth import require_auth

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Unique run suffix to isolate test data.
RUN_ID = secrets.token_hex(4)

# Generated credentials for this run.
HOD_EMP_ID = f"EMP-HOD-{RUN_ID}"
HOD_EMAIL = f"hod_{RUN_ID}@gcu.edu.pk"
HOD_PASS = "HodSecurePass123!"

FACULTY_EMP_ID = f"EMP-FAC-{RUN_ID}"
FACULTY_EMAIL = f"faculty_{RUN_ID}@gcu.edu.pk"
FACULTY_PASS = "FacultySecurePass123!"

STUDENT_ROLL = f"ROLL-{RUN_ID}"
STUDENT_BATCH = "2022-2026"
STUDENT_EMAIL = f"student_{RUN_ID}@gcu.edu.pk"
STUDENT_PASS = "StudentSecurePass123!"

# State across test steps (tokens, etc.)
TOKENS = {
    "hod": None,
    "faculty": None,
    "student": None,
}

# IPFS CIDs of the profile metadata pinned at registration (Phase 1.6, D7).
PROFILE_CIDS = {
    "hod": None,
    "faculty": None,
    "student": None,
}

# CIDv0 (Qm..., base58) or CIDv1 (b..., base32) - guards the `ipfs cat` call.
CID_PATTERN = re.compile(r"[A-Za-z0-9]{46,120}")

# Local app instance for DB operations & role gate unit verification.
db_app = create_app()

# Dedicated test app to verify require_auth(roles=[...]) decorator behavior.
role_gate_app = Flask(f"role_gate_test_{RUN_ID}")
role_gate_app.config["JWT_SECRET"] = db_app.config["JWT_SECRET"]


@role_gate_app.route("/hod-only")
@require_auth(roles=["HOD"])
def _hod_only_view():
    return jsonify({"status": "allowed", "role": g.role, "userId": g.user_id}), 200


role_gate_client = role_gate_app.test_client()


def api(method: str, path: str, **kwargs) -> tuple[int, dict]:
    """Call the live backend; returns (status_code, json_dict)."""
    response = requests.request(method, f"{BASE_URL}{path}", timeout=10, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def ipfs_cat(cid: str) -> str:
    """Read a pinned document back from the local IPFS daemon (`ipfs cat`)."""
    url = f"{db_app.config['IPFS_API_URL']}/api/v0/cat"
    try:
        response = requests.post(url, params={"arg": cid}, timeout=20)
    except requests.RequestException as exc:
        raise AssertionError(f"IPFS daemon unreachable at {url} ({exc}) - start `ipfs daemon`")
    assert response.status_code == 200, (
        f"ipfs cat {cid} returned {response.status_code}: {response.text[:200]}"
    )
    return response.text


def cleanup() -> None:
    """Delete all database rows created by this specific test run."""
    with db_app.app_context():
        # Delete profile child rows first
        StudentProfile.query.filter(
            StudentProfile.roll_number.like(f"%{RUN_ID}%")
        ).delete(synchronize_session=False)

        FacultyProfile.query.filter(
            FacultyProfile.employee_id.like(f"%{RUN_ID}%")
        ).delete(synchronize_session=False)

        # Delete user rows
        User.query.filter(
            User.email.like(f"%{RUN_ID}%")
        ).delete(synchronize_session=False)

        # Delete temporary whitelist entry (never touch pre-seeded demo rows)
        InstitutionWhitelist.query.filter(
            InstitutionWhitelist.employee_id.like(f"%{RUN_ID}%")
        ).delete(synchronize_session=False)

        db.session.commit()

        # Sanity check: confirm pre-seeded demo whitelist entries remain intact
        remaining_wl = InstitutionWhitelist.query.count()
        assert remaining_wl >= 3, f"Demo whitelist rows corrupted! Remaining count: {remaining_wl}"


# --------------------------------------------------------------------------
# Test Steps
# --------------------------------------------------------------------------
def step_preflight():
    status, payload = api("GET", "/health")
    assert status == 200, f"Expected 200 from /health, got {status}: {payload}"
    assert payload.get("status") == "ok", f"Health status not ok: {payload}"


def step_hod_unwhitelisted_rejected():
    status, payload = api(
        "POST",
        "/auth/register/hod",
        json={
            "employee_id": HOD_EMP_ID,
            "department": "Computer Science",
            "full_name": "Test HOD",
            "email": HOD_EMAIL,
            "password": HOD_PASS,
        },
    )
    assert status == 403, f"Expected 403 for unwhitelisted HOD, got {status}: {payload}"
    assert payload.get("check") == "whitelist", f"Expected check 'whitelist', got: {payload}"
    assert payload.get("error") == "not_whitelisted", f"Expected error 'not_whitelisted', got: {payload}"


def step_hod_whitelisted_accepted():
    # Insert temporary whitelist row for this run
    with db_app.app_context():
        wl_row = InstitutionWhitelist(
            employee_id=HOD_EMP_ID,
            department="Computer Science",
            name=f"Dr. HOD {RUN_ID}",
            email=HOD_EMAIL,
        )
        db.session.add(wl_row)
        db.session.commit()

    status, payload = api(
        "POST",
        "/auth/register/hod",
        json={
            "employee_id": HOD_EMP_ID,
            "department": "Computer Science",
            "full_name": f"Dr. HOD {RUN_ID}",
            "email": HOD_EMAIL,
            "password": HOD_PASS,
        },
    )
    assert status == 201, f"Expected 201 for whitelisted HOD, got {status}: {payload}"
    assert payload.get("user", {}).get("role") == "HOD", f"Expected role HOD: {payload}"
    assert payload.get("user", {}).get("email") == HOD_EMAIL, f"Email mismatch: {payload}"
    PROFILE_CIDS["hod"] = payload.get("profile_cid")


def step_hod_duplicate_rejected():
    status, payload = api(
        "POST",
        "/auth/register/hod",
        json={
            "employee_id": HOD_EMP_ID,
            "department": "Computer Science",
            "full_name": f"Dr. HOD {RUN_ID}",
            "email": HOD_EMAIL,
            "password": HOD_PASS,
        },
    )
    assert status == 409, f"Expected 409 for duplicate HOD email, got {status}: {payload}"
    assert payload.get("error") == "email_already_registered", f"Unexpected error: {payload}"


def step_faculty_bad_domain_rejected():
    status, payload = api(
        "POST",
        "/auth/register/faculty",
        json={
            "employee_id": FACULTY_EMP_ID,
            "full_name": f"Prof. Faculty {RUN_ID}",
            "department": "Computer Science",
            "email": f"faculty_{RUN_ID}@gmail.com",
            "password": FACULTY_PASS,
        },
    )
    assert status == 400, f"Expected 400 for bad faculty domain, got {status}: {payload}"
    assert payload.get("check") == "email", f"Expected check 'email', got: {payload}"
    assert payload.get("error") == "invalid_email_domain", f"Expected error 'invalid_email_domain', got: {payload}"


def step_faculty_valid_accepted():
    status, payload = api(
        "POST",
        "/auth/register/faculty",
        json={
            "employee_id": FACULTY_EMP_ID,
            "full_name": f"Prof. Faculty {RUN_ID}",
            "department": "Computer Science",
            "email": FACULTY_EMAIL,
            "password": FACULTY_PASS,
        },
    )
    assert status == 201, f"Expected 201 for valid faculty, got {status}: {payload}"
    assert payload.get("user", {}).get("role") == "FACULTY", f"Role mismatch: {payload}"
    assert payload.get("profile", {}).get("employeeId") == FACULTY_EMP_ID, f"Profile mismatch: {payload}"
    PROFILE_CIDS["faculty"] = payload.get("profile_cid")


def step_faculty_duplicate_emp_id_rejected():
    status, payload = api(
        "POST",
        "/auth/register/faculty",
        json={
            "employee_id": FACULTY_EMP_ID,
            "full_name": "Another Faculty",
            "department": "CS",
            "email": f"different_{RUN_ID}@gcu.edu.pk",
            "password": FACULTY_PASS,
        },
    )
    assert status == 409, f"Expected 409 for duplicate employee_id, got {status}: {payload}"
    assert payload.get("error") == "employee_id_already_registered", f"Unexpected error: {payload}"


def step_faculty_duplicate_email_rejected():
    status, payload = api(
        "POST",
        "/auth/register/faculty",
        json={
            "employee_id": f"DIFF-EMP-{RUN_ID}",
            "full_name": "Another Faculty",
            "department": "CS",
            "email": FACULTY_EMAIL,
            "password": FACULTY_PASS,
        },
    )
    assert status == 409, f"Expected 409 for duplicate email, got {status}: {payload}"
    assert payload.get("error") == "email_already_registered", f"Unexpected error: {payload}"


def step_student_bad_domain_rejected():
    status, payload = api(
        "POST",
        "/auth/register/student",
        json={
            "full_name": f"Student {RUN_ID}",
            "roll_number": STUDENT_ROLL,
            "batch": STUDENT_BATCH,
            "department": "Computer Science",
            "email": f"student_{RUN_ID}@yahoo.com",
            "password": STUDENT_PASS,
        },
    )
    assert status == 400, f"Expected 400 for bad student domain, got {status}: {payload}"
    assert payload.get("check") == "email", f"Expected check 'email', got: {payload}"
    assert payload.get("error") == "invalid_email_domain", f"Expected error 'invalid_email_domain', got: {payload}"


def step_student_valid_accepted():
    status, payload = api(
        "POST",
        "/auth/register/student",
        json={
            "full_name": f"Student {RUN_ID}",
            "roll_number": STUDENT_ROLL,
            "batch": STUDENT_BATCH,
            "department": "Computer Science",
            "email": STUDENT_EMAIL,
            "password": STUDENT_PASS,
        },
    )
    assert status == 201, f"Expected 201 for valid student, got {status}: {payload}"
    assert payload.get("user", {}).get("role") == "STUDENT", f"Role mismatch: {payload}"
    assert payload.get("profile", {}).get("rollNumber") == STUDENT_ROLL, f"Roll mismatch: {payload}"
    PROFILE_CIDS["student"] = payload.get("profile_cid")


def step_student_duplicate_roll_rejected():
    status, payload = api(
        "POST",
        "/auth/register/student",
        json={
            "full_name": "Another Student",
            "roll_number": STUDENT_ROLL,
            "batch": STUDENT_BATCH,
            "department": "CS",
            "email": f"different_student_{RUN_ID}@gcu.edu.pk",
            "password": STUDENT_PASS,
        },
    )
    assert status == 409, f"Expected 409 for duplicate roll_number, got {status}: {payload}"
    assert payload.get("error") == "roll_number_already_registered", f"Unexpected error: {payload}"


def step_student_duplicate_email_rejected():
    status, payload = api(
        "POST",
        "/auth/register/student",
        json={
            "full_name": "Another Student",
            "roll_number": f"DIFF-ROLL-{RUN_ID}",
            "batch": STUDENT_BATCH,
            "department": "CS",
            "email": STUDENT_EMAIL,
            "password": STUDENT_PASS,
        },
    )
    assert status == 409, f"Expected 409 for duplicate student email, got {status}: {payload}"
    assert payload.get("error") == "email_already_registered", f"Unexpected error: {payload}"


def step_login_hod_success():
    status, payload = api(
        "POST",
        "/auth/login",
        json={"email": HOD_EMAIL, "password": HOD_PASS},
    )
    assert status == 200, f"Expected 200 for HOD login, got {status}: {payload}"
    assert "token" in payload, f"Missing token in login response: {payload}"
    assert payload.get("user", {}).get("role") == "HOD", f"Role mismatch: {payload}"
    TOKENS["hod"] = payload["token"]


def step_login_faculty_success():
    status, payload = api(
        "POST",
        "/auth/login",
        json={"email": FACULTY_EMAIL, "password": FACULTY_PASS},
    )
    assert status == 200, f"Expected 200 for Faculty login, got {status}: {payload}"
    assert "token" in payload, f"Missing token in login response: {payload}"
    assert payload.get("user", {}).get("role") == "FACULTY", f"Role mismatch: {payload}"
    TOKENS["faculty"] = payload["token"]


def step_login_student_success():
    status, payload = api(
        "POST",
        "/auth/login",
        json={"email": STUDENT_EMAIL, "password": STUDENT_PASS},
    )
    assert status == 200, f"Expected 200 for Student login, got {status}: {payload}"
    assert "token" in payload, f"Missing token in login response: {payload}"
    assert payload.get("user", {}).get("role") == "STUDENT", f"Role mismatch: {payload}"
    TOKENS["student"] = payload["token"]


def step_login_wrong_password_rejected():
    status, payload = api(
        "POST",
        "/auth/login",
        json={"email": FACULTY_EMAIL, "password": "WrongPassword999!"},
    )
    assert status == 401, f"Expected 401 for wrong password, got {status}: {payload}"
    assert payload.get("error") == "invalid_credentials", f"Unexpected error: {payload}"
    assert "Invalid email or password" in payload.get("message", ""), f"Leaked status: {payload}"


def step_login_nonexistent_email_rejected():
    status, payload = api(
        "POST",
        "/auth/login",
        json={"email": f"nobody_{RUN_ID}@gcu.edu.pk", "password": "AnyPassword!"},
    )
    assert status == 401, f"Expected 401 for nonexistent email, got {status}: {payload}"
    assert payload.get("error") == "invalid_credentials", f"Unexpected error: {payload}"
    assert "Invalid email or password" in payload.get("message", ""), f"Leaked status: {payload}"


def step_me_missing_token_rejected():
    status, payload = api("GET", "/auth/me")
    assert status == 401, f"Expected 401 for missing token, got {status}: {payload}"
    assert payload.get("check") == "auth", f"Expected check 'auth', got: {payload}"
    assert payload.get("error") == "missing_token", f"Expected error 'missing_token', got: {payload}"


def step_me_invalid_token_rejected():
    status, payload = api(
        "GET",
        "/auth/me",
        headers={"Authorization": "Bearer malformed.jwt.token"},
    )
    assert status == 401, f"Expected 401 for invalid token, got {status}: {payload}"
    assert payload.get("error") == "invalid_token", f"Expected error 'invalid_token', got: {payload}"


def step_me_expired_token_rejected():
    now = int(time.time())
    expired_payload = {
        "sub": "9999",
        "user_id": 9999,
        "role": "STUDENT",
        "iat": now - 7200,
        "exp": now - 3600,
    }
    expired_jwt = jwt.encode(
        expired_payload,
        db_app.config["JWT_SECRET"],
        algorithm="HS256",
    )
    status, payload = api(
        "GET",
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_jwt}"},
    )
    assert status == 401, f"Expected 401 for expired token, got {status}: {payload}"
    assert payload.get("error") == "token_expired", f"Expected error 'token_expired', got: {payload}"


def step_me_hod_success():
    status, payload = api(
        "GET",
        "/auth/me",
        headers={"Authorization": f"Bearer {TOKENS['hod']}"},
    )
    assert status == 200, f"Expected 200 for HOD /auth/me, got {status}: {payload}"
    assert payload.get("user", {}).get("email") == HOD_EMAIL, f"Email mismatch: {payload}"
    assert payload.get("user", {}).get("role") == "HOD", f"Role mismatch: {payload}"


def step_me_faculty_success():
    status, payload = api(
        "GET",
        "/auth/me",
        headers={"Authorization": f"Bearer {TOKENS['faculty']}"},
    )
    assert status == 200, f"Expected 200 for Faculty /auth/me, got {status}: {payload}"
    assert payload.get("user", {}).get("email") == FACULTY_EMAIL, f"Email mismatch: {payload}"
    assert payload.get("profile", {}).get("employeeId") == FACULTY_EMP_ID, f"Profile mismatch: {payload}"


def step_me_student_success():
    status, payload = api(
        "GET",
        "/auth/me",
        headers={"Authorization": f"Bearer {TOKENS['student']}"},
    )
    assert status == 200, f"Expected 200 for Student /auth/me, got {status}: {payload}"
    assert payload.get("user", {}).get("email") == STUDENT_EMAIL, f"Email mismatch: {payload}"
    assert payload.get("profile", {}).get("rollNumber") == STUDENT_ROLL, f"Profile mismatch: {payload}"


def step_registration_returns_profile_cid():
    for role in ("hod", "faculty", "student"):
        cid = PROFILE_CIDS[role]
        assert cid, f"Registration response for {role} carried no profile_cid: {PROFILE_CIDS}"
        assert CID_PATTERN.fullmatch(cid), f"profile_cid for {role} is not a CID: {cid!r}"

    # /auth/me must echo the very same CID that registration returned.
    for role in ("hod", "faculty", "student"):
        status, payload = api(
            "GET",
            "/auth/me",
            headers={"Authorization": f"Bearer {TOKENS[role]}"},
        )
        assert status == 200, f"Expected 200 for {role} /auth/me, got {status}: {payload}"
        assert payload.get("profile_cid") == PROFILE_CIDS[role], (
            f"/auth/me profile_cid mismatch for {role}: {payload}"
        )


def step_profile_metadata_has_name_without_email():
    cases = (
        ("hod", f"Dr. HOD {RUN_ID}", HOD_EMAIL, HOD_PASS, HOD_EMP_ID),
        ("faculty", f"Prof. Faculty {RUN_ID}", FACULTY_EMAIL, FACULTY_PASS, FACULTY_EMP_ID),
        ("student", f"Student {RUN_ID}", STUDENT_EMAIL, STUDENT_PASS, STUDENT_ROLL),
    )
    for role, full_name, email, password, identifier in cases:
        document = ipfs_cat(PROFILE_CIDS[role])
        assert full_name in document, f"{role} profile document lacks the name: {document}"
        assert identifier in document, f"{role} profile document lacks its identifier: {document}"
        assert email not in document, f"{role} profile document leaks the email: {document}"
        assert password not in document, f"{role} profile document leaks the password: {document}"
        assert "password" not in document.lower(), (
            f"{role} profile document mentions a password field: {document}"
        )
        assert "email" not in document.lower(), (
            f"{role} profile document mentions an email field: {document}"
        )


def step_require_auth_student_blocked():
    resp = role_gate_client.get(
        "/hod-only",
        headers={"Authorization": f"Bearer {TOKENS['student']}"},
    )
    assert resp.status_code == 403, f"Expected 403 for student on HOD route, got {resp.status_code}: {resp.json}"
    assert resp.json.get("check") == "auth", f"Expected check 'auth', got: {resp.json}"
    assert resp.json.get("error") == "insufficient_role", f"Expected 'insufficient_role', got: {resp.json}"


def step_require_auth_faculty_blocked():
    resp = role_gate_client.get(
        "/hod-only",
        headers={"Authorization": f"Bearer {TOKENS['faculty']}"},
    )
    assert resp.status_code == 403, f"Expected 403 for faculty on HOD route, got {resp.status_code}: {resp.json}"
    assert resp.json.get("check") == "auth", f"Expected check 'auth', got: {resp.json}"
    assert resp.json.get("error") == "insufficient_role", f"Expected 'insufficient_role', got: {resp.json}"


def step_require_auth_hod_accepted():
    resp = role_gate_client.get(
        "/hod-only",
        headers={"Authorization": f"Bearer {TOKENS['hod']}"},
    )
    assert resp.status_code == 200, f"Expected 200 for HOD on HOD route, got {resp.status_code}: {resp.json}"
    assert resp.json.get("role") == "HOD", f"Role mismatch: {resp.json}"


STEPS = [
    ("Preflight: GET /api/v1/health reachable", step_preflight),
    ("HOD register: unwhitelisted credentials rejected (403)", step_hod_unwhitelisted_rejected),
    ("HOD register: whitelisted credentials accepted (201)", step_hod_whitelisted_accepted),
    ("HOD register: duplicate email rejected (409)", step_hod_duplicate_rejected),
    ("Faculty register: invalid email domain rejected (400)", step_faculty_bad_domain_rejected),
    ("Faculty register: valid registration accepted (201)", step_faculty_valid_accepted),
    ("Faculty register: duplicate employee_id rejected (409)", step_faculty_duplicate_emp_id_rejected),
    ("Faculty register: duplicate email rejected (409)", step_faculty_duplicate_email_rejected),
    ("Student register: invalid email domain rejected (400)", step_student_bad_domain_rejected),
    ("Student register: valid registration accepted (201)", step_student_valid_accepted),
    ("Student register: duplicate roll_number rejected (409)", step_student_duplicate_roll_rejected),
    ("Student register: duplicate email rejected (409)", step_student_duplicate_email_rejected),
    ("Login: HOD login succeeds with valid JWT (200)", step_login_hod_success),
    ("Login: Faculty login succeeds with valid JWT (200)", step_login_faculty_success),
    ("Login: Student login succeeds with valid JWT (200)", step_login_student_success),
    ("Login: wrong password rejected with generic 401", step_login_wrong_password_rejected),
    ("Login: nonexistent email rejected with generic 401", step_login_nonexistent_email_rejected),
    ("/auth/me: missing Authorization header rejected (401)", step_me_missing_token_rejected),
    ("/auth/me: malformed token rejected (401)", step_me_invalid_token_rejected),
    ("/auth/me: expired token rejected (401)", step_me_expired_token_rejected),
    ("/auth/me: valid HOD token returns user info (200)", step_me_hod_success),
    ("/auth/me: valid Faculty token returns user + profile (200)", step_me_faculty_success),
    ("/auth/me: valid Student token returns user + profile (200)", step_me_student_success),
    ("IPFS profile: registration + /auth/me return a profile_cid", step_registration_returns_profile_cid),
    ("IPFS profile: pinned document has the name, never the email", step_profile_metadata_has_name_without_email),
    ("require_auth(roles): wrong-role STUDENT blocked (403)", step_require_auth_student_blocked),
    ("require_auth(roles): wrong-role FACULTY blocked (403)", step_require_auth_faculty_blocked),
    ("require_auth(roles): matching-role HOD accepted (200)", step_require_auth_hod_accepted),
]


def main() -> int:
    print("=" * 65)
    print("   AUTHENTICATION & ROLE GOVERNANCE TESTS (test_auth.py)")
    print(f"   Target: {BASE_URL}  |  Run ID: {RUN_ID}")
    print("=" * 65)

    failed = False
    try:
        for index, (title, action) in enumerate(STEPS):
            print(f"\n{index:02d}. {title}")
            try:
                action()
            except requests.RequestException as exc:
                print(f"    \u274c FAIL: cannot reach backend at {BASE_URL} ({exc})")
                print("       start it first:  cd backend;  py -3.12 run.py")
                print("\n" + "=" * 65)
                print(f"   TEST RUN FAILED AT STEP {index:02d}: {title}")
                print("=" * 65)
                failed = True
                return 1
            except AssertionError as exc:
                print(f"    \u274c FAIL: {exc}")
                print("\n" + "=" * 65)
                print(f"   TEST RUN FAILED AT STEP {index:02d}: {title}")
                print("=" * 65)
                failed = True
                return 1
            except Exception as exc:
                print(f"    \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
                print("\n" + "=" * 65)
                print(f"   TEST RUN FAILED AT STEP {index:02d}: {title}")
                print("=" * 65)
                failed = True
                return 1
            print("    \u2705 PASS")
    finally:
        print("\n" + "-" * 65)
        print("   Running test database cleanup...")
        try:
            cleanup()
            print("   \u2705 Cleanup complete: removed test run rows, demo whitelist intact.")
        except Exception as exc:
            print(f"   \u274c Cleanup failed: {exc}")
            failed = True
        print("-" * 65)

    if not failed:
        print("\n" + "=" * 65)
        print(f"   ALL AUTH TESTS PASSED ({len(STEPS)}/{len(STEPS)} steps)")
        print("=" * 65)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
