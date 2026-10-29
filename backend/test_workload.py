"""End-to-end test of the Module 3 faculty workload flow.

Run it against a live stack (from backend/):

    py -3.12 test_workload.py

Prerequisites:
    - Hardhat node running       (contracts/: npm run node)
    - WorkloadLedger deployed    (contracts/: npm run deploy:workload)
    - Flask backend running      (backend/: py -3.12 run.py)
    - WORKLOAD_LEDGER_CONTRACT_ADDRESS set in backend/.env

Steps (each prints a clear PASS/FAIL line; the first failure stops the run
with the failing response, and the script exits non-zero - no uncaught
exceptions):

     0. preflight              - GET /health (backend reachable, DB + RPC +
                                  workload ledger configured)
     1. seed classes           - ScheduledClass rows inserted directly,
                                  one dated occurrence per scenario
     2. start (missing field)  - rejected 400 missing_fields        [request]
     3. start (wrong prof)     - rejected 403 not_class_professor   [professor]
     4. start (happy)          - 201: live session + rotating QR for the class
     5. scan (wrong nonce)     - rejected 400 nonce_mismatch        [session_nonce]
     6. projector QR           - the EXISTING Module 1 endpoint
                                  GET /api/v1/session/<id>/qr serves the
                                  workload session's rotating nonce
     7. scan (happy)           - 201: CONDUCTED anchored on-chain with a real
                                  tx hash, credited to the professor
     8. scan again             - 200 already_logged (idempotent per class)
     9. zero (never started)   - rejected 409 session_not_started   [session]
    10. zero (happy)           - 201: CONDUCTED_ZERO_STUDENTS anchored on-chain
    11. zero (concluded)       - rejected 409 workload_already_logged
    12. issue (wrong prof)     - rejected 403 not_class_professor   [professor]
    13. issue (happy)          - 201: 30-minute JWT for one named substitute
    14. redeem (wrong sub)     - rejected 403 substitute_mismatch   [substitute]
    15. redeem (happy)         - 201: substitute starts the session
    16. scan (sub's session)   - 201: CONDUCTED credited to the PRIMARY
                                  professor; conductedBy is the substitute
    17. redeem again           - rejected 409 token_already_redeemed
    18. expired token          - rejected 401 token_expired         [substitute_token]
    19. malformed token        - rejected 401 token_invalid         [substitute_token]
    20. auto-flag              - the background job function is called DIRECTLY
                                  (in-process, not on the scheduler's 60s tick)
                                  with UNCONDUCTED_THRESHOLD_MINUTES overridden
                                  to 1 for this run; the overdue class gets
                                  UNCONDUCTED anchored on-chain automatically
    21. logs listing           - GET /faculty/logs shows every outcome with
                                  its class details and tx hash, primary
                                  credit and named substitute conductor
    22. colleagues             - same-department FACULTY only; no self,
                                  students, HOD or other department; STUDENT 403
    23. issue to self          - primary professor cannot delegate to themselves

Design notes:
    - The test mixes HTTP calls (the real server) with a few in-process
      calls against the same Postgres DB: seeding ScheduledClass rows,
      minting an expired token, calling flag_unconducted_classes() directly,
      and reading WorkloadLedger.getWorkloadLog() as the source of truth.
      SCHEDULER_ENABLED=false is set before importing `app` so the test
      process never starts a second scheduler (the live server keeps its
      own).
    - The auto-flag class is seeded with startTime = now - 3 minutes: past
      the test's 1-minute threshold, but still inside the live server's
      15-minute default, so only the manual job call can flag it during the
      run (no race with the real scheduler).
    - professorId/conductorId are real FACULTY User ids. Preflight registers
      and logs in unique-email professor, substitute, stranger, student and
      HOD accounts. It seeds the matching Student.id == User.id row.
    - API checks carry real login tokens; public registration/login bootstrap
      requests are the only exception. Users are retained with their student
      row and workload logs; this script never resets users in isolation.
"""

import os
import sys
from datetime import timedelta
from uuid import uuid4

# Must be set before `app` is imported (the config reads it at import time):
# this process is a one-off test client, never a second scheduler host.
os.environ["SCHEDULER_ENABLED"] = "false"

import requests  # noqa: E402 (imported after the env tweak, like reset_local_state.py)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import InstitutionWhitelist, ScheduledClass, SessionLog, Student, User, UserRole  # noqa: E402
from app.scheduler import flag_unconducted_classes  # noqa: E402
from app.services import substitute_token, workload  # noqa: E402
from app.util import utcnow  # noqa: E402

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Filled with real User ids and login tokens by inline auth setup.
PROFESSOR_ID = None
SUBSTITUTE_ID = None
STRANGER_ID = None
AUTH = {"faculty_tokens": {}}
RUN_ID = uuid4().hex

# A short threshold for the auto-flag step (minutes). The live server keeps
# its own (default 15); this only affects the manual in-process job call.
TEST_THRESHOLD_MINUTES = 1

# In-process app against the same Postgres DB the live server uses.
APP = None  # initialized inside preflight, not as an import side effect

# Shared state between steps (filled in as the run progresses).
STATE = {
    "happy_class_id": None,
    "zero_class_id": None,
    "no_start_class_id": None,
    "sub_class_id": None,
    "autoflag_class_id": None,
    "session_id": None,
    "substitute_token": None,
    "sub_session_id": None,
    "conducted_log": None,
}


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Call the API with a login token, except during public auth bootstrap."""
    token = token or AUTH.get("professor_token")
    if not path.startswith("/auth/"):
        assert token, f"no login token available for {method} {path}"
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=15, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def start_session(professor_id: int, class_id: int, body_professor_id: int | None = None) -> tuple[int, dict]:
    """Start as the token owner; a body professor id is only a spoofing probe."""
    body = {"class_id": class_id}
    if body_professor_id is not None:
        body["professor_id"] = body_professor_id
    return api("POST", "/faculty/session/start", token=AUTH["faculty_tokens"][professor_id], json=body)


def scan(session_id: int, nonce: str, student_id: int | None = None) -> tuple[int, dict]:
    """POST /faculty/session/scan with the scanned nonce."""
    body = {"session_id": session_id, "nonce": nonce}
    if student_id is not None:
        body["student_id"] = student_id
    return api("POST", "/faculty/session/scan", token=AUTH["student_token"], json=body)


def current_nonce(session_id: int) -> str:
    """Fetch the live QR nonce from the EXISTING Module 1 projector endpoint."""
    status, payload = api("GET", f"/session/{session_id}/qr")
    assert status == 200, f"GET /session/{session_id}/qr returned {status}: {payload}"
    nonce = payload.get("current_nonce")
    assert nonce, f"no current_nonce in QR payload: {payload}"
    return nonce


def zero_attendance(professor_id: int, class_id: int) -> tuple[int, dict]:
    """Declare as the token owner; a body id cannot grant conductor rights."""
    return api(
        "POST", "/faculty/session/zero-attendance",
        token=AUTH["faculty_tokens"][professor_id],
        json={"professor_id": PROFESSOR_ID, "class_id": class_id},
    )


def issue_token(professor_id: int, class_id: int, substitute_id: int) -> tuple[int, dict]:
    """Issue as the token owner, deliberately sending another professor's body id."""
    body_professor_id = STRANGER_ID if professor_id == PROFESSOR_ID else PROFESSOR_ID
    return api(
        "POST", "/faculty/substitute/issue",
        token=AUTH["faculty_tokens"][professor_id],
        json={"professor_id": body_professor_id, "class_id": class_id, "substitute_id": substitute_id},
    )


def redeem_token(token: str, substitute_id: int) -> tuple[int, dict]:
    """Redeem as the login-token owner, not the deliberately spoofed body id."""
    body_substitute_id = STRANGER_ID if substitute_id == SUBSTITUTE_ID else SUBSTITUTE_ID
    return api(
        "POST", "/faculty/substitute/redeem", token=AUTH["faculty_tokens"][substitute_id],
        json={"token": token, "substitute_id": body_substitute_id},
    )


def onchain_log(class_id: int) -> dict:
    """Read WorkloadLedger.getWorkloadLog() in-process - the source of truth."""
    with APP.app_context():
        return workload.get_workload_log(class_id)


def log_for(class_id: int) -> dict | None:
    """The SessionLog entry for a class from GET /faculty/logs (or None)."""
    status, payload = api("GET", "/faculty/logs", token=AUTH["hod_token"])
    assert status == 200, f"GET /faculty/logs returned {status}: {payload}"
    for entry in payload.get("logs") or []:
        if entry.get("classId") == class_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def setup_auth():
    """Register/login real users inline, using unique emails and profile ids."""
    global APP, PROFESSOR_ID, SUBSTITUTE_ID, STRANGER_ID
    APP = create_app()
    domain = APP.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    password = "WorkloadTestPassword123!"
    accounts = {}
    for persona in ("professor", "substitute", "stranger", "student", "hod", "other_department"):
        account = {
            "email": f"workload.{persona}.{RUN_ID}@{domain}", "password": password,
            "full_name": f"Workload Test {persona.title()}", "department": "Computer Science",
        }
        if persona == "other_department":
            account["department"] = "Mathematics"
        if persona == "student":
            account.update(roll_number=f"WL-STUDENT-{RUN_ID}", batch="2022-2026")
        else:
            account["employee_id"] = f"WL-{persona.upper()}-{RUN_ID}"
        accounts[persona] = account
    with APP.app_context():
        hod = accounts["hod"]
        db.session.add(InstitutionWhitelist(
            employee_id=hod["employee_id"], email=hod["email"],
            name=hod["full_name"], department=hod["department"],
        ))
        db.session.commit()
    for persona, account in accounts.items():
        role = persona if persona in ("student", "hod") else "faculty"
        status, payload = api("POST", f"/auth/register/{role}", json=account)
        assert status == 201, f"{persona} registration failed ({status}): {payload}"
        assert payload["user"]["role"] == role.upper(), f"wrong registration role: {payload}"
        status, payload = api(
            "POST", "/auth/login", json={"email": account["email"], "password": password},
        )
        assert status == 200, f"{persona} login failed ({status}): {payload}"
        assert payload["user"]["role"] == role.upper(), f"wrong login role: {payload}"
        assert payload.get("token"), f"{persona} login returned no token: {payload}"
        AUTH[f"{persona}_token"] = payload["token"]
        AUTH[f"{persona}_id"] = payload["user"]["id"]
        AUTH[f"{persona}_full_name"] = account["full_name"]
        if role == "faculty":
            AUTH["faculty_tokens"][payload["user"]["id"]] = payload["token"]
    PROFESSOR_ID = AUTH["professor_id"]
    SUBSTITUTE_ID = AUTH["substitute_id"]
    STRANGER_ID = AUTH["stranger_id"]
    with APP.app_context():
        student_id = AUTH["student_id"]
        assert db.session.get(Student, student_id) is None, (
            f"student id {student_id} already exists; do not overwrite unrelated attendance data"
        )
        db.session.add(Student(id=student_id, name=accounts["student"]["full_name"]))
        db.session.commit()
    print(
        f"   authenticated professor={PROFESSOR_ID} substitute={SUBSTITUTE_ID} "
        f"stranger={STRANGER_ID} student={AUTH['student_id']} hod={AUTH['hod_id']}"
    )


def step_preflight():
    """Authenticate inline, then check DB + blockchain RPC + workload ledger."""
    setup_auth()
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    assert deps["contracts_configured"]["workload_ledger"], (
        "workload ledger contract address is not configured (run `npm run deploy:workload` "
        "and set WORKLOAD_LEDGER_CONTRACT_ADDRESS)"
    )
    print(
        f"   service={payload['service']} env={payload['environment']} "
        f"db={deps['database_configured']} rpc={deps['blockchain_rpc_configured']} "
        f"workload_ledger_contract={deps['contracts_configured']['workload_ledger']}"
    )


def step_seed_classes():
    """One faculty-owned absolute-time ScheduledClass per scenario."""
    with APP.app_context():
        def seed(professor_id: int, minutes_from_now: float, course: str) -> int:
            start = utcnow() + timedelta(minutes=minutes_from_now) - timedelta(seconds=5)
            scheduled_class = ScheduledClass(
                professor_id=professor_id,
                room="Room 204",
                start_time=start,
                end_time=start + timedelta(hours=1),
                day_of_week=None,
                course_code=course,
            )
            db.session.add(scheduled_class)
            db.session.commit()
            return scheduled_class.id

        STATE["happy_class_id"] = seed(PROFESSOR_ID, 0, "BSCS-401")
        STATE["zero_class_id"] = seed(PROFESSOR_ID, 0, "BSCS-402")
        STATE["no_start_class_id"] = seed(PROFESSOR_ID, 0, "BSCS-403")
        STATE["sub_class_id"] = seed(PROFESSOR_ID, 0, "BSCS-404")
        # 3 minutes in the past: past the test's 1-minute threshold, but
        # inside the live server's 15-minute default (see module docstring).
        STATE["autoflag_class_id"] = seed(PROFESSOR_ID, -3, "BSCS-405")

    print(
        f"   classes seeded: happy={STATE['happy_class_id']} zero={STATE['zero_class_id']} "
        f"no_start={STATE['no_start_class_id']} substitute={STATE['sub_class_id']} "
        f"autoflag={STATE['autoflag_class_id']} (startTime now - 3 min)"
    )


def step_start_missing_fields():
    """A start without class_id never creates a session."""
    status, payload = api("POST", "/faculty/session/start", json={})
    assert status == 400, f"missing-field start must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "missing_fields", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_start_wrong_professor():
    """Only the scheduled professor may start the class."""
    status, payload = start_session(STRANGER_ID, STATE["happy_class_id"], body_professor_id=PROFESSOR_ID)
    assert status == 403, f"wrong-professor start must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "not_class_professor", f"unexpected error: {payload}"
    assert payload.get("check") == "professor", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_start_happy():
    """The happy start: a live rotating-QR session owned by the professor."""
    status, payload = start_session(PROFESSOR_ID, STATE["happy_class_id"], body_professor_id=STRANGER_ID)
    assert status == 201, f"start returned {status}: {payload}"

    session = payload.get("session") or {}
    workload_session = payload.get("workload_session") or {}
    assert session.get("is_active") is True, f"session not active: {session}"
    assert session.get("class_id") == f"workload-{STATE['happy_class_id']}", (
        f"session not namespaced to the scheduled class: {session}"
    )
    assert workload_session.get("conductorId") == PROFESSOR_ID, (
        f"conductor is not the professor: {workload_session}"
    )
    assert payload.get("qr_poll_url") == f"/api/v1/session/{session.get('id')}/qr", (
        f"bad qr_poll_url: {payload}"
    )
    assert isinstance(payload.get("rotation_seconds"), int) and payload["rotation_seconds"] > 0, (
        f"bad rotation_seconds: {payload}"
    )

    STATE["session_id"] = session.get("id")
    assert isinstance(STATE["session_id"], int), f"bad session id: {payload}"
    print(
        f"   session {STATE['session_id']} live (qr at {payload['qr_poll_url']}, "
        f"rotates every {payload['rotation_seconds']}s, conductor=professor {workload_session['conductorId']})"
    )


def step_scan_wrong_nonce():
    """Only STUDENT tokens can scan; a stale nonce still fails its original gate."""
    status, payload = api(
        "POST", "/faculty/session/scan", token=AUTH["professor_token"],
        json={"session_id": STATE["session_id"], "nonce": current_nonce(STATE["session_id"])},
    )
    assert status == 403 and payload.get("error") == "insufficient_role", f"FACULTY scan allowed: {payload}"
    assert payload.get("check") == "auth", f"wrong FACULTY scan gate: {payload}"
    status, payload = scan(STATE["session_id"], "not-the-current-nonce")
    assert status == 400, f"wrong-nonce scan must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "nonce_mismatch", f"unexpected error: {payload}"
    assert payload.get("check") == "session_nonce", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_projector_qr():
    """The existing Module 1 QR endpoint serves the workload session."""
    nonce = current_nonce(STATE["session_id"])
    assert "." in nonce, f"nonce does not look like a freshly rotated one: {nonce}"
    print(f"   GET /session/{STATE['session_id']}/qr -> nonce {nonce[:18]}... (Module 1 endpoint, reused)")


def step_scan_happy():
    """The happy scan: CONDUCTED anchored on-chain with a real tx hash."""
    status, payload = scan(STATE["session_id"], current_nonce(STATE["session_id"]), student_id=STRANGER_ID)
    assert status == 201, f"scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected scan status: {payload}"
    assert payload.get("student_id") == AUTH["student_id"], f"scanner came from body id: {payload}"

    session_log = payload.get("session_log") or {}
    assert session_log.get("status") == "CONDUCTED", f"session_log status wrong: {session_log}"
    assert session_log.get("conductedBy") == PROFESSOR_ID, f"wrong conductor: {session_log}"
    tx_hash = session_log.get("onchainTxHash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad onchainTxHash: {session_log}"
    assert isinstance(payload.get("block_number"), int) and payload["block_number"] > 0, (
        f"bad block_number: {payload}"
    )

    # Source of truth: the entry is readable straight from the contract.
    onchain = onchain_log(STATE["happy_class_id"])
    assert onchain["logged"] is True, f"not logged on-chain: {onchain}"
    assert onchain["status"] == "CONDUCTED", f"on-chain status wrong: {onchain}"
    assert onchain["professor_id"] == PROFESSOR_ID, f"on-chain professor wrong: {onchain}"

    STATE["conducted_log"] = session_log
    print(f"   CONDUCTED on-chain: tx={tx_hash} block={payload['block_number']} conductor=professor {PROFESSOR_ID}")


def step_scan_duplicate():
    """A second scan of the same session is an idempotent no-op."""
    status, payload = scan(STATE["session_id"], current_nonce(STATE["session_id"]))
    assert status == 200, f"duplicate scan must return 200 already_logged, got {status}: {payload}"
    assert payload.get("status") == "already_logged", f"unexpected status: {payload}"
    assert payload.get("student_id") == AUTH["student_id"], f"wrong authenticated scanner: {payload}"
    session_log = payload.get("session_log") or {}
    assert session_log.get("id") == STATE["conducted_log"].get("id"), (
        f"already_logged returned a different entry: {session_log}"
    )
    print(f"   idempotent: same session_log #{session_log.get('id')} returned, no second transaction")


def step_zero_without_start():
    """Zero attendance without a started session: rejected."""
    status, payload = zero_attendance(PROFESSOR_ID, STATE["no_start_class_id"])
    assert status == 409, f"zero-without-start must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "session_not_started", f"unexpected error: {payload}"
    assert payload.get("check") == "session", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_zero_happy():
    """The happy zero declaration: CONDUCTED_ZERO_STUDENTS anchored on-chain."""
    status, payload = start_session(PROFESSOR_ID, STATE["zero_class_id"])
    assert status == 201, f"start returned {status}: {payload}"

    status, payload = zero_attendance(STRANGER_ID, STATE["zero_class_id"])
    assert status == 403, f"non-conductor zero declaration allowed: {payload}"
    assert payload.get("error") == "not_session_conductor", f"wrong conductor gate: {payload}"
    assert payload.get("check") == "professor", f"wrong conductor check: {payload}"
    status, payload = zero_attendance(PROFESSOR_ID, STATE["zero_class_id"])
    assert status == 201, f"zero-attendance returned {status}: {payload}"
    assert payload.get("status") == "conducted_zero_students", f"unexpected status: {payload}"

    session_log = payload.get("session_log") or {}
    assert session_log.get("status") == "CONDUCTED_ZERO_STUDENTS", f"session_log status wrong: {session_log}"
    assert session_log.get("conductedBy") == PROFESSOR_ID, f"wrong conductor: {session_log}"
    tx_hash = session_log.get("onchainTxHash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad onchainTxHash: {session_log}"

    onchain = onchain_log(STATE["zero_class_id"])
    assert onchain["logged"] is True and onchain["status"] == "CONDUCTED_ZERO_STUDENTS", (
        f"on-chain state wrong: {onchain}"
    )
    print(f"   CONDUCTED_ZERO_STUDENTS on-chain: tx={tx_hash} conductor=professor {session_log['conductedBy']}")


def step_zero_already_concluded():
    """Declaring zero on a concluded class: rejected (one entry per class)."""
    status, payload = zero_attendance(PROFESSOR_ID, STATE["zero_class_id"])
    assert status == 409, f"second zero declaration must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "workload_already_logged", f"unexpected error: {payload}"
    assert payload.get("check") == "session_log", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_issue_wrong_professor():
    """A stranger cannot issue a substitute token for someone else's class."""
    status, payload = issue_token(STRANGER_ID, STATE["sub_class_id"], SUBSTITUTE_ID)
    assert status == 403, f"wrong-professor issue must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "not_class_professor", f"unexpected error: {payload}"
    assert payload.get("check") == "professor", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_issue_happy():
    """Only an existing FACULTY substitute can receive the 30-minute token."""
    status, payload = issue_token(PROFESSOR_ID, STATE["sub_class_id"], 999_999_999)
    assert status == 404, f"unknown substitute must be rejected: {payload}"
    assert payload.get("error") == "substitute_not_found", f"wrong missing substitute gate: {payload}"
    status, payload = issue_token(PROFESSOR_ID, STATE["sub_class_id"], AUTH["student_id"])
    assert status == 403, f"non-FACULTY substitute must be rejected: {payload}"
    assert payload.get("error") == "substitute_not_faculty", f"wrong substitute role gate: {payload}"
    status, payload = issue_token(PROFESSOR_ID, STATE["sub_class_id"], SUBSTITUTE_ID)
    assert status == 201, f"issue returned {status}: {payload}"

    token = payload.get("substitute_token")
    assert token and token.count(".") == 2, f"token is not a JWT: {payload}"
    assert payload.get("substitute_id") == SUBSTITUTE_ID, f"wrong substitute_id: {payload}"
    assert payload.get("issued_by") == PROFESSOR_ID, f"issuer came from body id: {payload}"
    assert payload.get("class_id") == STATE["sub_class_id"], f"wrong class_id: {payload}"
    assert payload.get("expires_at"), f"no expires_at: {payload}"
    assert payload.get("expires_in_minutes") == 30, f"token lifetime is not 30 minutes: {payload}"

    STATE["substitute_token"] = token
    print(f"   token issued for substitute {SUBSTITUTE_ID}, class {STATE['sub_class_id']} (expires in 30 min)")


def step_redeem_wrong_substitute():
    """Only the substitute named in the token may redeem it."""
    status, payload = redeem_token(STATE["substitute_token"], STRANGER_ID)
    assert status == 403, f"wrong-substitute redeem must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "substitute_mismatch", f"unexpected error: {payload}"
    assert payload.get("check") == "substitute", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_redeem_happy():
    """The happy redeem: the substitute starts the session as conductor."""
    status, payload = redeem_token(STATE["substitute_token"], SUBSTITUTE_ID)
    assert status == 201, f"redeem returned {status}: {payload}"
    assert payload.get("status") == "redeemed", f"unexpected status: {payload}"
    assert payload.get("substitute_id") == SUBSTITUTE_ID, f"wrong substitute: {payload}"
    assert payload.get("courseCode") == "BSCS-404", f"wrong covered course: {payload}"
    assert payload.get("room") == "Room 204", f"wrong covered room: {payload}"
    assert payload.get("primaryProfessorName") == AUTH["professor_full_name"], (
        f"wrong primary professor name: {payload}"
    )

    session = payload.get("session") or {}
    workload_session = payload.get("workload_session") or {}
    assert session.get("is_active") is True, f"session not active: {session}"
    assert workload_session.get("conductorId") == SUBSTITUTE_ID, (
        f"conductor is not the substitute: {workload_session}"
    )

    STATE["sub_session_id"] = session.get("id")
    status, denied = zero_attendance(PROFESSOR_ID, STATE["sub_class_id"])
    assert status == 403 and denied.get("error") == "not_session_conductor", (
        f"scheduled professor must not declare zero after substitute takes over: {denied}"
    )
    print(
        f"   session {STATE['sub_session_id']} started by substitute {SUBSTITUTE_ID} "
        f"(qr at {payload.get('qr_poll_url')})"
    )


def step_scan_substitute_session():
    """D1: PRIMARY professor credited; substitute recorded as conductor."""
    status, payload = scan(STATE["sub_session_id"], current_nonce(STATE["sub_session_id"]))
    assert status == 201, f"scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected status: {payload}"

    session_log = payload.get("session_log") or {}
    assert session_log.get("status") == "CONDUCTED", f"session_log status wrong: {session_log}"
    # D1 deliberately replaces the old substitute-credit expectation.
    assert session_log.get("professorId") == PROFESSOR_ID, (
        f"workload credited to {session_log.get('professorId')}, expected primary professor {PROFESSOR_ID}"
    )
    assert session_log.get("conductedBy") == SUBSTITUTE_ID, (
        f"conductor is {session_log.get('conductedBy')}, expected substitute {SUBSTITUTE_ID}"
    )
    assert session_log.get("conductedBy") != PROFESSOR_ID, "primary professor incorrectly recorded as conductor"
    with APP.app_context():
        mirror = SessionLog.query.filter_by(class_id=STATE["sub_class_id"]).one()
        assert mirror.professor_id == PROFESSOR_ID, f"DB credit wrong: {mirror.to_dict()}"
        assert mirror.conducted_by == SUBSTITUTE_ID, f"DB conductor wrong: {mirror.to_dict()}"

    onchain = onchain_log(STATE["sub_class_id"])
    assert onchain["logged"] is True and onchain["status"] == "CONDUCTED", f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == PROFESSOR_ID, (
        f"on-chain credit went to professor {onchain['professor_id']}, expected primary professor {PROFESSOR_ID}"
    )
    print(
        f"   CONDUCTED on-chain credited to primary professor {PROFESSOR_ID}, "
        f"conducted by substitute {SUBSTITUTE_ID} (tx={session_log.get('onchainTxHash')})"
    )


def step_redeem_twice():
    """A redeemed token cannot be redeemed again (one-shot)."""
    status, payload = redeem_token(STATE["substitute_token"], SUBSTITUTE_ID)
    assert status == 409, f"second redeem must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "token_already_redeemed", f"unexpected error: {payload}"
    assert payload.get("check") == "substitute_token", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_expired_token():
    """An expired token is rejected (minted in-process with a negative TTL)."""
    with APP.app_context():
        expired_token, _ = substitute_token.mint(
            PROFESSOR_ID, SUBSTITUTE_ID, STATE["no_start_class_id"], ttl_minutes=-1
        )
    status, payload = redeem_token(expired_token, SUBSTITUTE_ID)
    assert status == 401, f"expired token must be rejected with 401, got {status}: {payload}"
    assert payload.get("error") == "substitute_token_expired", f"unexpected error: {payload}"
    assert payload.get("check") == "substitute_token", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_malformed_token():
    """A garbage token is rejected."""
    status, payload = redeem_token("not-a-jwt", SUBSTITUTE_ID)
    assert status == 401, f"malformed token must be rejected with 401, got {status}: {payload}"
    assert payload.get("error") == "substitute_token_invalid", f"unexpected error: {payload}"
    assert payload.get("check") == "substitute_token", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_autoflag():
    """The background auto-flag, called directly with a 1-minute threshold."""
    # Override the threshold in-process (the live server keeps its own
    # 15-minute default); the job reads app.config at call time.
    APP.config["UNCONDUCTED_THRESHOLD_MINUTES"] = TEST_THRESHOLD_MINUTES
    flagged = flag_unconducted_classes(APP)
    assert flagged >= 1, "the auto-flag job flagged nothing (expected the overdue class)"

    entry = log_for(STATE["autoflag_class_id"])
    assert entry is not None, (
        f"no SessionLog for the overdue class {STATE['autoflag_class_id']} after the job ran"
    )
    assert entry.get("status") == "UNCONDUCTED", f"auto-flag status wrong: {entry}"
    assert entry.get("conductedBy") == PROFESSOR_ID, f"auto-flag credit wrong: {entry}"
    assert str(entry.get("onchainTxHash", "")).startswith("0x"), f"no tx hash on the auto-flag entry: {entry}"

    onchain = onchain_log(STATE["autoflag_class_id"])
    assert onchain["logged"] is True and onchain["status"] == "UNCONDUCTED", f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == PROFESSOR_ID, f"on-chain credit wrong: {onchain}"

    # Already-concluded classes must NOT be re-flagged (one entry per class).
    happy_entry = log_for(STATE["happy_class_id"])
    assert happy_entry.get("status") == "CONDUCTED", f"happy class was re-flagged: {happy_entry}"

    print(
        f"   class {STATE['autoflag_class_id']} (startTime now - 3 min, threshold "
        f"{TEST_THRESHOLD_MINUTES} min) auto-flagged UNCONDUCTED on-chain: tx={entry['onchainTxHash']}"
    )


def step_logs_listing():
    """HOD can audit every outcome; FACULTY cannot access the audit log."""
    status, denied = api("GET", "/faculty/logs", token=AUTH["professor_token"])
    assert status == 403 and denied.get("error") == "insufficient_role", f"FACULTY logs allowed: {denied}"
    assert denied.get("check") == "auth", f"wrong logs auth gate: {denied}"
    status, payload = api("GET", "/faculty/logs", token=AUTH["hod_token"])
    assert status == 200, f"GET /faculty/logs returned {status}: {payload}"

    logs = payload.get("logs") or []
    assert payload.get("count") == len(logs), f"count mismatch: {payload}"

    by_class = {entry.get("classId"): entry for entry in logs}
    for class_id, expected in (
        (STATE["happy_class_id"], "CONDUCTED"),
        (STATE["zero_class_id"], "CONDUCTED_ZERO_STUDENTS"),
        (STATE["sub_class_id"], "CONDUCTED"),
        (STATE["autoflag_class_id"], "UNCONDUCTED"),
    ):
        entry = by_class.get(class_id)
        assert entry is not None, f"class {class_id} missing from /faculty/logs: {sorted(by_class)}"
        assert entry.get("status") == expected, f"class {class_id}: expected {expected}, got {entry}"
        assert entry.get("class"), f"no class details on the entry: {entry}"
        assert entry.get("class", {}).get("courseId"), f"no courseId on the class details: {entry}"

    substitute_entry = by_class[STATE["sub_class_id"]]
    assert substitute_entry.get("professorId") == PROFESSOR_ID, f"substitute class credit wrong: {substitute_entry}"
    assert substitute_entry.get("conductedBy") == SUBSTITUTE_ID, f"substitute conductor missing: {substitute_entry}"
    assert substitute_entry.get("conductedByName") == AUTH["substitute_full_name"], (
        f"substitute conductor name wrong: {substitute_entry}"
    )
    assert substitute_entry.get("substituted") is True, f"substituted flag missing: {substitute_entry}"
    for class_id in (STATE["happy_class_id"], STATE["zero_class_id"], STATE["autoflag_class_id"]):
        assert by_class[class_id].get("professorId") == PROFESSOR_ID, f"primary credit wrong: {by_class[class_id]}"
        assert by_class[class_id].get("substituted") is False, f"false substitute flag: {by_class[class_id]}"

    # The never-started class (used for negatives) must NOT have an entry.
    assert STATE["no_start_class_id"] not in by_class, (
        f"no-start class was logged unexpectedly: {by_class.get(STATE['no_start_class_id'])}"
    )
    print(
        f"   {payload.get('count')} log entries: "
        + ", ".join(
            f"{by_class[cid]['class'].get('courseId')}={by_class[cid]['status']}"
            for cid in (
                STATE["happy_class_id"],
                STATE["zero_class_id"],
                STATE["sub_class_id"],
                STATE["autoflag_class_id"],
            )
        )
    )


def step_colleagues():
    """The caller sees only other FACULTY users in their own department."""
    status, payload = api("GET", "/faculty/colleagues", token=AUTH["professor_token"])
    assert status == 200, f"GET /faculty/colleagues returned {status}: {payload}"
    colleagues = payload.get("colleagues")
    assert isinstance(colleagues, list), f"colleagues list missing: {payload}"
    assert payload.get("count") == len(colleagues), f"colleagues count mismatch: {payload}"
    ids = [entry["id"] for entry in colleagues]
    assert len(ids) == len(set(ids)), f"duplicate colleagues: {colleagues}"
    assert {SUBSTITUTE_ID, STRANGER_ID}.issubset(ids), f"same-department faculty missing: {colleagues}"
    assert not {PROFESSOR_ID, AUTH["student_id"], AUTH["hod_id"], AUTH["other_department_id"]}.intersection(ids), (
        f"self, student, HOD or other-department user leaked into colleagues: {colleagues}"
    )
    # Verify all results, not only the seeded users, against the source rows.
    with APP.app_context():
        caller = db.session.get(User, PROFESSOR_ID)
        expected = {
            user.id: user
            for user in User.query.filter_by(department=caller.department).all()
            if user.role == UserRole.FACULTY and user.id != PROFESSOR_ID
        }
        assert set(ids) == set(expected), f"colleagues ids {ids}, expected {sorted(expected)}"
        for entry in colleagues:
            assert set(entry) == {"id", "full_name", "employee_id"}, f"wrong colleague fields: {entry}"
            user = expected[entry["id"]]
            assert entry["full_name"] == user.full_name, f"wrong colleague name: {entry}"
            employee_id = user.faculty_profile.employee_id if user.faculty_profile else None
            assert entry["employee_id"] == employee_id, f"wrong colleague employee id: {entry}"

    status, denied = api("GET", "/faculty/colleagues", token=AUTH["student_token"])
    assert status == 403, f"STUDENT colleagues request allowed: {denied}"
    assert denied.get("check") == "auth", f"wrong colleagues auth check: {denied}"
    assert denied.get("error") == "insufficient_role", f"wrong colleagues role error: {denied}"
    print(f"   {len(colleagues)} other same-department faculty; STUDENT rejected with 403")


def step_issue_self():
    """The issuer is the primary professor; neither can be the substitute."""
    status, denied = issue_token(PROFESSOR_ID, STATE["no_start_class_id"], PROFESSOR_ID)
    assert status == 400, f"self/primary delegation allowed: {denied}"
    assert denied.get("check") == "request", f"wrong self-delegation check: {denied}"
    assert denied.get("error") == "invalid_substitute", f"wrong self-delegation error: {denied}"
    print("   self/primary-professor delegation rejected with 400 request/invalid_substitute")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: register/login real users + backend /health", step_preflight),
    ("Seed scheduled classes", step_seed_classes),
    ("Start session without class_id -> blocked", step_start_missing_fields),
    ("Start session by the wrong professor -> blocked", step_start_wrong_professor),
    ("Start session (professor) -> live rotating-QR session", step_start_happy),
    ("Scan with a wrong nonce -> blocked (QR rotation gate)", step_scan_wrong_nonce),
    ("Projector QR served by the existing Module 1 endpoint", step_projector_qr),
    ("Student scan -> CONDUCTED anchored on-chain", step_scan_happy),
    ("Second scan -> idempotent already_logged", step_scan_duplicate),
    ("Zero attendance without a started session -> blocked", step_zero_without_start),
    ("Zero attendance declaration -> CONDUCTED_ZERO_STUDENTS on-chain", step_zero_happy),
    ("Zero attendance on a concluded class -> blocked", step_zero_already_concluded),
    ("Substitute token issued by the wrong professor -> blocked", step_issue_wrong_professor),
    ("Validate FACULTY substitute + issue 30-min signed JWT", step_issue_happy),
    ("Redeem by the wrong substitute -> blocked", step_redeem_wrong_substitute),
    ("Redeem token -> substitute starts the session", step_redeem_happy),
    ("Scan the substitute's session -> PRIMARY credit, substitute conductor", step_scan_substitute_session),
    ("Redeem the same token again -> blocked", step_redeem_twice),
    ("Expired substitute token -> blocked", step_expired_token),
    ("Malformed substitute token -> blocked", step_malformed_token),
    ("Auto-flag job (manual call, 1-min threshold) -> UNCONDUCTED on-chain", step_autoflag),
    ("Logs listing: every outcome with class details", step_logs_listing),
    ("Colleagues: same department only; STUDENT -> 403", step_colleagues),
    ("Substitute token issued to self/primary professor -> blocked", step_issue_self),
]


def main() -> int:
    print("=" * 60)
    print("   FACULTY WORKLOAD LEDGER TESTS (test_workload.py)")
    print("=" * 60)

    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            print("      start it first:  py -3.12 run.py   (from backend/)")
            print("\n" + "=" * 60)
            print(f"   TEST RUN FAILED AT STEP {index}: {title}")
            print("=" * 60)
            return 1
        except AssertionError as exc:
            print(f"   \u274c FAIL: {exc}")
            print("\n" + "=" * 60)
            print(f"   TEST RUN FAILED AT STEP {index}: {title}")
            print("=" * 60)
            return 1
        except Exception as exc:  # keep the run free of uncaught exceptions
            print(f"   \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
            print("\n" + "=" * 60)
            print(f"   TEST RUN FAILED AT STEP {index}: {title}")
            print("=" * 60)
            return 1
        print("   \u2705 PASS")

    print("\n" + "=" * 60)
    print(f"   ALL FACULTY WORKLOAD TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
