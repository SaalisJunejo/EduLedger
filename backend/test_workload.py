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
     1. seed classes           - ScheduledClass rows inserted directly (the
                                  module deliberately has no class-creation
                                  endpoint; timetable ingestion is future
                                  scope), one per scenario
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
    16. scan (sub's session)   - 201: CONDUCTED credited to the SUBSTITUTE
                                  (conductedBy != the scheduled professor)
    17. redeem again           - rejected 409 token_already_redeemed
    18. expired token          - rejected 401 token_expired         [substitute_token]
    19. malformed token        - rejected 401 token_invalid         [substitute_token]
    20. auto-flag              - the background job function is called DIRECTLY
                                  (in-process, not on the scheduler's 60s tick)
                                  with UNCONDUCTED_THRESHOLD_MINUTES overridden
                                  to 1 for this run; the overdue class gets
                                  UNCONDUCTED anchored on-chain automatically
    21. logs listing           - GET /faculty/logs shows every outcome with
                                  its class details and tx hash

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
    - professorId is an opaque integer (no professor table exists yet),
      exactly like studentId in the Module 2 tests.
"""

import os
import sys
from datetime import timedelta

# Must be set before `app` is imported (the config reads it at import time):
# this process is a one-off test client, never a second scheduler host.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import requests  # noqa: E402 (imported after the env tweak, like reset_local_state.py)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import ScheduledClass  # noqa: E402
from app.scheduler import flag_unconducted_classes  # noqa: E402
from app.services import substitute_token, workload  # noqa: E402
from app.util import utcnow  # noqa: E402

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Scenario personas: professor 101 owns the classes, 102 substitutes, 999 is
# a stranger with no rights anywhere.
PROFESSOR_ID = 101
SUBSTITUTE_ID = 102
STRANGER_ID = 999

# A short threshold for the auto-flag step (minutes). The live server keeps
# its own (default 15); this only affects the manual in-process job call.
TEST_THRESHOLD_MINUTES = 1

# In-process app against the same Postgres DB the live server uses.
APP = create_app()

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
def api(method: str, path: str, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    response = requests.request(method, f"{BASE_URL}{path}", timeout=15, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def start_session(professor_id: int, class_id: int) -> tuple[int, dict]:
    """POST /faculty/session/start."""
    return api("POST", "/faculty/session/start", json={"professor_id": professor_id, "class_id": class_id})


def scan(session_id: int, nonce: str, student_id: int | None = None) -> tuple[int, dict]:
    """POST /faculty/session/scan with the scanned nonce."""
    body = {"session_id": session_id, "nonce": nonce}
    if student_id is not None:
        body["student_id"] = student_id
    return api("POST", "/faculty/session/scan", json=body)


def current_nonce(session_id: int) -> str:
    """Fetch the live QR nonce from the EXISTING Module 1 projector endpoint."""
    status, payload = api("GET", f"/session/{session_id}/qr")
    assert status == 200, f"GET /session/{session_id}/qr returned {status}: {payload}"
    nonce = payload.get("current_nonce")
    assert nonce, f"no current_nonce in QR payload: {payload}"
    return nonce


def zero_attendance(professor_id: int, class_id: int) -> tuple[int, dict]:
    """POST /faculty/session/zero-attendance."""
    return api(
        "POST", "/faculty/session/zero-attendance", json={"professor_id": professor_id, "class_id": class_id}
    )


def issue_token(professor_id: int, class_id: int, substitute_id: int) -> tuple[int, dict]:
    """POST /faculty/substitute/issue."""
    return api(
        "POST",
        "/faculty/substitute/issue",
        json={"professor_id": professor_id, "class_id": class_id, "substitute_id": substitute_id},
    )


def redeem_token(token: str, substitute_id: int) -> tuple[int, dict]:
    """POST /faculty/substitute/redeem."""
    return api("POST", "/faculty/substitute/redeem", json={"token": token, "substitute_id": substitute_id})


def onchain_log(class_id: int) -> dict:
    """Read WorkloadLedger.getWorkloadLog() in-process - the source of truth."""
    with APP.app_context():
        return workload.get_workload_log(class_id)


def log_for(class_id: int) -> dict | None:
    """The SessionLog entry for a class from GET /faculty/logs (or None)."""
    status, payload = api("GET", "/faculty/logs")
    assert status == 200, f"GET /faculty/logs returned {status}: {payload}"
    for entry in payload.get("logs") or []:
        if entry.get("classId") == class_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Backend reachable and configured (DB + blockchain RPC + workload ledger)."""
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
    """One ScheduledClass per scenario, inserted directly (no creation endpoint)."""
    with APP.app_context():
        def seed(professor_id: int, minutes_from_now: float, course: str) -> int:
            scheduled_class = ScheduledClass(
                professor_id=professor_id,
                room="Room 204",
                start_time=utcnow() + timedelta(minutes=minutes_from_now),
                course_id=course,
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
    """A start without professor_id never creates a session."""
    status, payload = api("POST", "/faculty/session/start", json={"class_id": STATE["happy_class_id"]})
    assert status == 400, f"missing-field start must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "missing_fields", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_start_wrong_professor():
    """Only the scheduled professor may start the class."""
    status, payload = start_session(STRANGER_ID, STATE["happy_class_id"])
    assert status == 403, f"wrong-professor start must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "not_class_professor", f"unexpected error: {payload}"
    assert payload.get("check") == "professor", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_start_happy():
    """The happy start: a live rotating-QR session owned by the professor."""
    status, payload = start_session(PROFESSOR_ID, STATE["happy_class_id"])
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
    """A stale/guessed nonce is rejected - the rotating-QR gate is reused."""
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
    status, payload = scan(STATE["session_id"], current_nonce(STATE["session_id"]), student_id=2024001)
    assert status == 201, f"scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected scan status: {payload}"

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
    """The happy issue: a 30-minute signed JWT for one named substitute."""
    status, payload = issue_token(PROFESSOR_ID, STATE["sub_class_id"], SUBSTITUTE_ID)
    assert status == 201, f"issue returned {status}: {payload}"

    token = payload.get("substitute_token")
    assert token and token.count(".") == 2, f"token is not a JWT: {payload}"
    assert payload.get("substitute_id") == SUBSTITUTE_ID, f"wrong substitute_id: {payload}"
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

    session = payload.get("session") or {}
    workload_session = payload.get("workload_session") or {}
    assert session.get("is_active") is True, f"session not active: {session}"
    assert workload_session.get("conductorId") == SUBSTITUTE_ID, (
        f"conductor is not the substitute: {workload_session}"
    )

    STATE["sub_session_id"] = session.get("id")
    print(
        f"   session {STATE['sub_session_id']} started by substitute {SUBSTITUTE_ID} "
        f"(qr at {payload.get('qr_poll_url')})"
    )


def step_scan_substitute_session():
    """The substitute's class is CONDUCTED with credit to the SUBSTITUTE."""
    status, payload = scan(STATE["sub_session_id"], current_nonce(STATE["sub_session_id"]))
    assert status == 201, f"scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected status: {payload}"

    session_log = payload.get("session_log") or {}
    assert session_log.get("status") == "CONDUCTED", f"session_log status wrong: {session_log}"
    # The core substitute assertion: workload credit goes to the substitute,
    # NOT the originally scheduled professor.
    assert session_log.get("conductedBy") == SUBSTITUTE_ID, (
        f"workload credited to {session_log.get('conductedBy')}, expected the substitute {SUBSTITUTE_ID}"
    )
    assert session_log.get("conductedBy") != PROFESSOR_ID, "workload was credited to the scheduled professor"

    onchain = onchain_log(STATE["sub_class_id"])
    assert onchain["logged"] is True and onchain["status"] == "CONDUCTED", f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == SUBSTITUTE_ID, (
        f"on-chain credit went to professor {onchain['professor_id']}, expected substitute {SUBSTITUTE_ID}"
    )
    print(
        f"   CONDUCTED on-chain credited to substitute {SUBSTITUTE_ID} "
        f"(tx={session_log.get('onchainTxHash')}), not professor {PROFESSOR_ID}"
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
    """/faculty/logs lists every outcome with class details and tx hashes."""
    status, payload = api("GET", "/faculty/logs")
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


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Seed scheduled classes", step_seed_classes),
    ("Start session without professor_id -> blocked", step_start_missing_fields),
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
    ("Issue substitute token -> 30-min signed JWT", step_issue_happy),
    ("Redeem by the wrong substitute -> blocked", step_redeem_wrong_substitute),
    ("Redeem token -> substitute starts the session", step_redeem_happy),
    ("Scan the substitute's session -> credit to the SUBSTITUTE", step_scan_substitute_session),
    ("Redeem the same token again -> blocked", step_redeem_twice),
    ("Expired substitute token -> blocked", step_expired_token),
    ("Malformed substitute token -> blocked", step_malformed_token),
    ("Auto-flag job (manual call, 1-min threshold) -> UNCONDUCTED on-chain", step_autoflag),
    ("Logs listing: every outcome with class details", step_logs_listing),
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
