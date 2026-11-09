"""EduLedger full-stack smoke test - Stage 1 in one run.

The pre-demo safety net: ONE command that exercises every module against the
LIVE stack (Flask + Hardhat + local IPFS daemon + Redis), prints an unambiguous
PASS/FAIL summary with every on-chain transaction hash, and exits non-zero
if anything failed - so "demo-ready" is never a guess.

    cd backend
    py -3.12 smoke_test_full.py

Prerequisites (exact bring-up sequence: backend/DEMO_RESET.md):
    - Hardhat node running          (contracts/: npm run node)
    - all 5 contracts deployed      (canonical order, see DEMO_RESET.md)
    - real demo ADMIN bootstrapped BEFORE tests (this script never bootstraps)
    - Flask backend running         (backend/: py -3.12 run.py)
    - local Kubo daemon running     (ipfs daemon - Module 2 evidence upload)
    - PostgreSQL running            (Windows service)
    - Redis running                 (redis-cli ping - Module 1 live socket, D5)

The script registers/logs in unique faculty, substitute faculty, two students
and whitelisted HOD accounts inline. Every API request after public
registration/login carries the appropriate bearer token. It creates only
its OWN classes; the seeded demo roster is untouched. The D2 refusal check
uses a throwaway ADMIN DB row, never the permanent bootstrap lock. A finally
block cleans up its DB rows and its own local Kubo pins, even after failed
checks. No sequences are reset: new runs keep fresh user/session/class ids on
Postgres. On-chain transactions cannot be deleted, nor can distributed
IPFS copies; cleanup only unpins this run's own evidence (no global GC).

Steps
-----
  Setup
    S0    register + log in the five unique accounts
    S1    GET /governance/status -> 200 (read-only)

  Module 1 - Zero-Proxy Attendance
    M1.1  enroll the smoke student's face       (re-enroll is idempotent)
    M1.2  register the smoke device             (HOD rebind fallback)
    M1.3  verify identity -> 30s verification token
    M1.4  start an attendance session + fetch the live nonce
    M1.5  valid scan -> attendance locked ON-CHAIN          [tx]
    M1.6  duplicate scan -> rejected 409 attendance_already_locked
    M1.7  6-digit numeric code scan by a SECOND student      [tx]
    M1.8  conductor's socket receives student_checked_in     [tx]
    M1.9  monthly xlsx export contains that student
    M1.10 conductor ends the session; a later scan is refused

  Module 2 - Multi-Sig Audit Trail
    M2.1  upload a small evidence PNG to IPFS -> CID
    M2.2  propose a record change with that CID             [tx]
    M2.3  approve as HOD -> Executed on-chain               [tx]
    M2.4  the pending list no longer includes the proposal
    M2.5  grade correction: propose -> row locks -> HOD approves -> value
          changes, lock clears                               [tx, tx]
    M2.6  ADMIN cannot approve a correction (403)

  Module 3 - Faculty Workload Ledger
    M3.1  seed 2 fresh scheduled classes        (direct DB - no endpoint yet)
    M3.2  faculty starts the owned ScheduledClass session (Module 1 QR)
    M3.3  student scan -> class CONDUCTED on-chain           [tx, faculty credit]
    M3.4  issue a substitute key to the other real faculty account
    M3.5  substitute redeems the key using their own login token
    M3.6  scan -> CONDUCTED credited to the PRIMARY teacher, conductedBy
          is the substitute                                 [tx]
    M3.7  GET /faculty/logs as HOD shows both entries

  Module 4 - Device-Bound Biometric Credential (D4)
    M4.1  enroll the device secret -> challenge -> correct HMAC gives a
          token; a wrong HMAC is refused

  Cleanup
    C1    remove this run's fixtures and unpin its evidence

Total: 27/27 checks on success (2 setup + 10 attendance + 6 audit trail
+ 7 workload + 1 credential + cleanup). The health gate remains separate
from the numbered checks.

Failure isolation
-----------------
The four modules run in sequence, but a failing module never stops the next
one, and within a module the steps that depend on a failed step are SKIPped
- one run always gives the full picture. Shared auth or health failure
skips module steps that cannot run, with the exact infrastructure error.
Governance read failure does not stop the independent module checks.
Cleanup runs even when auth setup or the health gate fails.

Exit code: 0 = all steps passed (demo-ready), 1 = anything failed.

Output is ASCII-only on purpose (Windows console code pages + safe to paste
into reports); the stdout UTF-8 guard below follows the project convention.
"""

import base64
import hashlib
import hmac
import io
import os
import queue
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from PIL import Image, PngImagePlugin

# One-off client process: never a second scheduler host. Must be set before
# the (lazily imported) `app` package is loaded - the config reads it at
# import time, exactly like reset_local_state.py / seed_workload_demo.py.
os.environ["SCHEDULER_ENABLED"] = "false"

# Windows consoles often default to a legacy code page (cp1252); force UTF-8
# so the run never dies on a print (same guard as the other dev scripts).
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5000/api/v1")
# The Socket.IO root of the same server (no /api/v1 suffix) - Module 1's
# live-attendance client connects here, exactly like test_socket.py.
SOCKET_URL = os.environ.get("SMOKE_SOCKET_URL", BASE_URL.rsplit("/api/", 1)[0])
# The Kubo RPC the backend talks to (config default / backend/.env value) -
# probed by the health gate only as a WARNING, since the health endpoint
# checks configuration, not liveness.
IPFS_API_URL = os.environ.get("SMOKE_IPFS_API_URL", "http://127.0.0.1:5001")

# Real ids/tokens are filled by inline auth, never borrowed from demo users.
RUN_ID = uuid4().hex
RUN_STARTED = datetime.now(timezone.utc).replace(tzinfo=None)
PASSWORD = "SmokeTestPassword123!"
EMAIL_DOMAIN = "gcu.edu.pk"
STUDENT_ID = None
STUDENT2_ID = None
STUDENT_NAME = "Smoke Test Student"
STUDENT2_NAME = "Smoke Test Second Student"
STUDENT2_ROLL = f"SMOKE-STUDENT2-{RUN_ID}"
DEVICE_ID = f"smoke-test-device-{RUN_ID}"
DEVICE2_ID = f"smoke-test-device2-{RUN_ID}"
ATTENDANCE_CLASS = None
PROFESSOR_ID = None
SUBSTITUTE_ID = None
AUTH = {}
ACCOUNTS = {}
APP = None
STUDENT_ROW_OWNED = False
STUDENT2_ROW_OWNED = False
TEMP_ADMIN_EMAIL = f"smoke.admin.{RUN_ID}@{EMAIL_DOMAIN}"
TEMP_ADMIN_CREATED = False
COURSES = [f"SMOKE-{scenario}-{RUN_ID}" for scenario in ("ATT", "3A", "3B")]
# One more occurrence for the live-socket + export steps (M1.8/M1.9).
SOCKET_COURSE = f"SMOKE-SOCK-{RUN_ID}"
# The grade the corrections in M2.5/M2.6 act on.
GRADE_COURSE = f"SMOKE-GRADE-{RUN_ID}"
GRADE_OLD = "B"
GRADE_NEW = "A"
GRADE_SECOND = "B+"
GRADE_JUSTIFICATION = "Re-marked question 4 after the external examiner's review."

# Same CID grammar the IPFS test uses (CIDv0 "Qm..." from local Kubo,
# CIDv1 "baf..." from the Web3.Storage fallback).
CID_PATTERN = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-z2-7]{30,})$")

# Shared state between steps (filled in as the run progresses).
STATE = {
    "token": None,
    "attendance_session_id": None,
    "cid": None,
    "proposal_id": None,
    "conducted_class_id": None,
    "substitute_class_id": None,
    "faculty_session_id": None,
    "substitute_token": None,
    "substitute_session_id": None,
    "device2_secret": None,
    "socket_class_id": None,
    "socket_session_id": None,
    "grade_proposal_id": None,
    # IPFS pins this run created beyond the evidence PNG (grade-correction
    # JSON) - cleanup unpins them all.
    "extra_cids": [],
}

# What the summary table is built from: (step key, label, status, detail).
RESULTS: list[tuple[str, str, str, str]] = []
# (label, tx hash) - every transaction anchored during the run.
TRANSACTIONS: list[tuple[str, str]] = []


# ---------------------------------------------------------------------------
# Synthetic inputs (valid, decodable PNGs - the backend rejects placeholder
# base64 strings, so real bytes are required; same approach as
# test_identity_scan.py, and they work with the demo face embedder that is
# active when face_recognition is not installed).
# ---------------------------------------------------------------------------
def _png_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _gradient_image(shift: int = 0) -> Image.Image:
    """A smooth horizontal gradient - the smoke student's 'face'."""
    image = Image.new("L", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            pixels[x, y] = max(0, min(255, (x + shift) * 255 // 95))
    return image


def _evidence_png() -> bytes:
    """A small distinctive PNG standing in for a scanned evidence document."""
    image = Image.new("RGB", (160, 90), (245, 247, 250))
    pixels = image.load()
    for y in range(90):
        for x in range(160):
            if (x // 20 + y // 18) % 2 == 0:
                pixels[x, y] = (33, 75, 120)
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("smoke_run", RUN_ID)  # unique CID; never unpin someone else's evidence
    image.save(buffer, format="PNG", pnginfo=metadata)
    return buffer.getvalue()


ENROLL_IMAGE = _png_data_uri(_gradient_image(shift=0))
CAPTURE_IMAGE = _png_data_uri(_gradient_image(shift=2))  # same face, tiny capture difference
EVIDENCE_PNG = _evidence_png()


# ---------------------------------------------------------------------------
# API helpers (same never-raise conventions as the other test scripts)
# ---------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Carry a login token on every API call except public registration/login."""
    token = token or AUTH.get("professor_token")
    public_auth = path == "/auth/login" or path.startswith("/auth/register/")
    if not public_auth:
        assert token, f"no login token available for {method} {path}"
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(
        method, f"{BASE_URL}{path}", headers=headers, timeout=30, **kwargs,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


# persona -> /auth/register/<role>; two students keep the numeric-code and
# live-socket checks independent of the QR-lock checks.
PERSONAS = {
    "professor": "faculty",
    "substitute": "faculty",
    "student": "student",
    "student2": "student",
    "hod": "hod",
}


def setup_auth() -> str:
    """Register/login unique accounts, plus a test-only HOD whitelist entry."""
    global APP, PROFESSOR_ID, SUBSTITUTE_ID, STUDENT_ID, STUDENT2_ID
    global STUDENT_ROW_OWNED, EMAIL_DOMAIN, TEMP_ADMIN_EMAIL
    from app import create_app
    from app.extensions import db
    from app.models import InstitutionWhitelist, Student

    APP = create_app()
    domain = APP.config.get("INSTITUTION_EMAIL_DOMAIN", EMAIL_DOMAIN).strip().lower()
    EMAIL_DOMAIN = domain
    TEMP_ADMIN_EMAIL = f"smoke.admin.{RUN_ID}@{domain}"
    for persona, role in PERSONAS.items():
        account = {
            "email": f"smoke.{persona}.{RUN_ID}@{domain}", "password": PASSWORD,
            "full_name": f"Smoke Test {persona.title()}", "department": "Computer Science",
        }
        if role == "student":
            second = persona == "student2"
            account.update(
                roll_number=STUDENT2_ROLL if second else f"SMOKE-STUDENT-{RUN_ID}",
                batch="2022-2026",
            )
            account["full_name"] = STUDENT2_NAME if second else STUDENT_NAME
        else:
            account["employee_id"] = f"SMOKE-{persona.upper()}-{RUN_ID}"
        ACCOUNTS[persona] = account
    with APP.app_context():
        assert db.engine.dialect.name == "postgresql", (
            "use the live server's PostgreSQL DB_URL; SQLite may reuse deleted on-chain ids"
        )
        hod = ACCOUNTS["hod"]
        db.session.add(InstitutionWhitelist(
            employee_id=hod["employee_id"], email=hod["email"],
            name=hod["full_name"], department=hod["department"],
        ))
        db.session.commit()
    for persona, account in ACCOUNTS.items():
        role = PERSONAS[persona]
        status, payload = api("POST", f"/auth/register/{role}", json=account)
        assert status == 201, f"{persona} registration failed ({status}): {payload}"
        assert payload["user"]["role"] == role.upper(), f"wrong registration role: {payload}"
        registered_id = payload["user"]["id"]
        AUTH[f"{persona}_id"] = registered_id
        if persona == "hod":
            assert payload.get("verified") is True, f"HOD not auto-verified: {payload}"
        status, payload = api(
            "POST", "/auth/login", json={"email": account["email"], "password": PASSWORD},
        )
        assert status == 200, f"{persona} login failed ({status}): {payload}"
        assert payload["user"]["role"] == role.upper(), f"wrong login role: {payload}"
        assert payload["user"]["id"] == registered_id, f"login returned another user: {payload}"
        assert payload.get("token"), f"{persona} login returned no token: {payload}"
        AUTH[f"{persona}_token"] = payload["token"]
    PROFESSOR_ID = AUTH["professor_id"]
    SUBSTITUTE_ID = AUTH["substitute_id"]
    STUDENT_ID = AUTH["student_id"]
    STUDENT2_ID = AUTH["student2_id"]
    # Module 3 needs the identity bridge even if Module 1 later fails enrollment.
    with APP.app_context():
        assert db.session.get(Student, STUDENT_ID) is None, (
            f"Student {STUDENT_ID} already exists; refusing to overwrite unrelated data"
        )
        STUDENT_ROW_OWNED = True
        db.session.add(Student(id=STUDENT_ID, name=STUDENT_NAME))
        db.session.commit()
    return (f"faculty={PROFESSOR_ID}, substitute={SUBSTITUTE_ID}, "
            f"students={STUDENT_ID}/{STUDENT2_ID}, HOD={AUTH['hod_id']} (run {RUN_ID})")


def seed_class(course: str) -> int:
    """Like test_workload.py: a faculty-owned occurrence starting five seconds ago."""
    from app.extensions import db
    from app.models import ScheduledClass
    from app.util import utcnow

    with APP.app_context():
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=PROFESSOR_ID, room="Smoke Room", course_code=course,
            start_time=start, end_time=start + timedelta(hours=1), day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        return scheduled_class.id


def governance_status() -> str:
    """Read-only; never consume the permanent admin bootstrap lock."""
    status, payload = api("GET", "/governance/status", token=AUTH["hod_token"])
    assert status == 200, f"GET /governance/status returned {status}: {payload}"
    assert isinstance(payload.get("locked"), bool), f"missing locked flag: {payload}"
    assert isinstance(payload.get("adminUserExists"), bool), f"missing ADMIN flag: {payload}"
    assert re.fullmatch(r"0x[0-9a-fA-F]{40}", str(payload.get("rootAdmin"))), (
        f"invalid rootAdmin: {payload}"
    )
    return (f"locked={payload['locked']}, rootAdmin={payload['rootAdmin']}, "
            f"adminUserExists={payload['adminUserExists']}")


def qr_nonce(session_id: int, token: str | None = None) -> str:
    """The session's current live nonce (the QR endpoint lazily rotates
    expired nonces, so this always returns one the scan endpoints accept)."""
    status, payload = api("GET", f"/session/{session_id}/qr", token=token)
    assert status == 200, f"GET /session/{session_id}/qr returned {status}: {payload}"
    nonce = payload.get("current_nonce")
    assert nonce, f"no current_nonce in QR payload: {payload}"
    return nonce


def _scan_with_live_nonce(scan_fn, session_id: int, token: str | None = None) -> tuple[int, dict]:
    """Scan with a nonce fetched immediately before, retrying once if the 5s
    rotation window flipped between the read and the submit (the real QR
    would simply be rescanned; the retry keeps steps that test OTHER checks
    from failing on nonce timing)."""
    status, payload = scan_fn(qr_nonce(session_id, token=token))
    if payload.get("check") == "session_nonce":
        status, payload = scan_fn(qr_nonce(session_id, token=token))
    return status, payload


def attendance_scan(
    token: str, device_id: str, session_id: int, nonce: str,
    login_token: str | None = None,
) -> tuple[int, dict]:
    """POST /attendance/scan - field names per the API contract: 'nonce', and
    no student_id (the login and verification tokens identify the student)."""
    return api(
        "POST",
        "/attendance/scan",
        token=login_token or AUTH["student_token"],
        json={
            "verification_token": token,
            "device_id": device_id,
            "session_id": session_id,
            "nonce": nonce,
        },
    )


def faculty_scan(session_id: int, nonce: str) -> tuple[int, dict]:
    """POST /faculty/session/scan - any single scan concludes the class."""
    return api(
        "POST", "/faculty/session/scan", token=AUTH["student_token"],
        json={"session_id": session_id, "nonce": nonce},
    )


def attendance_scan_code(
    token: str, device_id: str, session_id: int | None, numeric_code: str,
    login_token: str | None = None,
) -> tuple[int, dict]:
    """POST /attendance/scan with the 6-digit code instead of the QR nonce."""
    body = {
        "verification_token": token,
        "device_id": device_id,
        "numeric_code": numeric_code,
    }
    if session_id is not None:
        body["session_id"] = session_id
    return api(
        "POST", "/attendance/scan",
        token=login_token or AUTH["student_token"], json=body,
    )


def api_download(path: str, token: str) -> tuple[int, bytes, dict]:
    """GET a binary response (the attendance export) without JSON parsing."""
    response = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    return response.status_code, response.content, dict(response.headers)


def export_month() -> str:
    """The current calendar month in the timetable zone (export's month param)."""
    zone = ZoneInfo(APP.config.get("TIMETABLE_TIMEZONE", "Asia/Karachi"))
    return datetime.now(zone).strftime("%Y-%m")


def device_challenge(login_token: str) -> str:
    """GET /students/identity/challenge - a single-use challenge (D4)."""
    status, payload = api("GET", "/students/identity/challenge", token=login_token)
    assert status == 200, f"students/identity/challenge returned {status}: {payload}"
    challenge = payload.get("challenge")
    assert isinstance(challenge, str) and challenge, f"no challenge in response: {payload}"
    return challenge


def device_response(secret: str, challenge: str, device_id: str) -> str:
    """hex(HMAC_SHA256(secret, challenge + ":" + device_id)) - the app's formula."""
    return hmac.new(
        secret.encode("utf-8"),
        f"{challenge}:{device_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_device(
    login_token: str, device_id: str, challenge: str, response: str,
) -> tuple[int, dict]:
    """POST /students/verify-identity-device with a computed HMAC response."""
    return api(
        "POST",
        "/students/verify-identity-device",
        token=login_token,
        json={"device_id": device_id, "challenge": challenge, "response": response},
    )


def device_identity_token(login_token: str, device_id: str, secret: str) -> str:
    """A fresh 30 s device-biometric verification token for one student."""
    challenge = device_challenge(login_token)
    status, payload = verify_device(
        login_token, device_id, challenge, device_response(secret, challenge, device_id),
    )
    assert status == 200, f"verify-identity-device returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    assert payload.get("method") == "device-biometric", f"unexpected method: {payload}"
    return token


class LiveSocket:
    """A socket.io client that records the live-attendance events it receives.

    Same handshake as test_socket.py: the API JWT travels in the connect
    payload (D5) and the conductor then joins one session's room.
    """

    EVENTS = ("joined", "join_error", "student_checked_in", "session_ended")

    def __init__(self, token: str | None):
        import socketio as socketio_client

        self.token = token
        self.received: queue.Queue = queue.Queue()
        self.sio = socketio_client.Client(reconnection=False)
        for event in self.EVENTS:
            self.sio.on(event, self._recorder(event))

    def _recorder(self, event: str):
        def handler(payload=None):
            self.received.put((event, payload))

        return handler

    def connect(self) -> None:
        self.sio.connect(
            SOCKET_URL,
            auth={"token": self.token} if self.token else None,
            wait_timeout=15,
        )

    def emit(self, event: str, payload: dict) -> None:
        self.sio.emit(event, payload)

    def wait_for(self, names, timeout: float = 10.0) -> tuple[str, dict]:
        """Block until one of `names` arrives, or fail the step."""
        if isinstance(names, str):
            names = (names,)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"no {' / '.join(names)} event within {timeout}s "
                    "(is Redis running for the SocketIO message queue?)"
                )
            try:
                event, payload = self.received.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if event in names:
                return event, payload

    def close(self) -> None:
        try:
            self.sio.disconnect()
        except Exception:  # noqa: BLE001 - closing a refused socket must not fail a step
            pass


def note_tx(label: str, tx_hash) -> str:
    """Validate + collect a transaction hash; returns the hash for printing."""
    tx = str(tx_hash)
    assert tx.startswith("0x") and len(tx) == 66, f"bad transaction hash: {tx!r}"
    TRANSACTIONS.append((label, tx))
    return tx


def _reason(exc: Exception) -> str:
    """One-line human reason for a failure."""
    if isinstance(exc, AssertionError):
        return str(exc) or "assertion failed"
    return f"{type(exc).__name__}: {exc}"


def _with_hint(detail: str) -> str:
    """Append the fix when a failure has a known cause."""
    if "already_logged" in detail:
        return detail + "  [hint: stale chain state - run the full reset in DEMO_RESET.md]"
    if "ipfs_daemon_unreachable" in detail or "ipfs_upload_failed" in detail:
        return detail + "  [hint: start the local IPFS daemon:  ipfs daemon]"
    if "too_early" in detail:
        return detail + "  [hint: check server UTC clock / TIMETABLE_TIMEZONE; see DEMO_RESET.md]"
    if "student_checked_in event within" in detail or "ConnectionError" in detail:
        return detail + (
            "  [hint: the live panel needs Redis (redis-cli ping) and an active session; "
            "see DEMO_RESET.md]"
        )
    if "not_enrolled" in detail:
        return detail + "  [hint: enroll the device secret first: POST /students/device/enroll]"
    return detail


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------
class Module:
    """Runs one module's steps. A failure skips the module's remaining steps
    (they depend on it) but never the next module."""

    def __init__(self, number: int, title: str):
        self.number = number
        self.title = title
        self.failed = False
        print("\n" + "-" * 78)
        print(f"MODULE {number} - {title}")
        print("-" * 78)

    def step(self, key: str, label: str, fn) -> None:
        if self.failed:
            RESULTS.append((key, label, "SKIP", "skipped - an earlier step in this module failed"))
            print(f"  SKIP  {key} {label}")
            return
        print(f"  ....  {key} {label}")
        try:
            detail = fn() or ""
        except Exception as exc:  # noqa: BLE001 - every failure becomes a FAIL row
            self.failed = True
            reason = _with_hint(_reason(exc))
            RESULTS.append((key, label, "FAIL", reason))
            print(f"  FAIL  {key} {label}")
            print(f"        {reason}")
            return
        RESULTS.append((key, label, "PASS", detail))
        print(f"  PASS  {key} {label}")
        if detail:
            print(f"        {detail}")


# ---------------------------------------------------------------------------
# Health gate (step 0) - the only hard abort
# ---------------------------------------------------------------------------
def health_gate() -> list[str]:
    """Verify the stack is configured. Returns the list of problems (empty
    list = ready to run the modules)."""
    problems: list[str] = []
    try:
        status, payload = api("GET", "/health")
    except requests.RequestException as exc:
        return [
            f"cannot reach the backend at {BASE_URL} ({exc.__class__.__name__})",
            "  fix: start it -  cd backend;  py -3.12 run.py",
        ]

    if status != 200:
        return [f"GET /health returned HTTP {status}: {payload}"]
    if payload.get("status") != "ok":
        problems.append(f"health status is {payload.get('status')!r}, not 'ok'")

    deps = payload.get("dependencies") or {}
    if not deps.get("database_configured"):
        problems.append("database is not configured (check DB_URL in backend/.env)")
    if not deps.get("blockchain_rpc_configured"):
        problems.append("blockchain RPC is not configured (check HARDHAT_RPC_URL in backend/.env)")

    contracts = deps.get("contracts_configured") or {}
    expected_contracts = {
        "attendance_engine": "npm run deploy:attendance",
        "audit_trail": "npm run deploy:audit-trail",
        "role_registry": "npm run deploy:local",
        "workload_ledger": "npm run deploy:workload",
        "governance": "npm run deploy:governance",
    }
    for name, deploy_command in expected_contracts.items():
        if not contracts.get(name):
            problems.append(
                f"contract '{name}' is not configured - deploy it from contracts/ "
                f"(`{deploy_command}`, canonical order in DEMO_RESET.md) and check "
                "backend/.env (restart Flask after .env changes)"
            )

    # The health flags are config checks, not liveness probes; the IPFS
    # daemon is the one dependency Module 2 needs that health cannot speak
    # for, so probe it directly - as a warning, not an abort.
    ipfs_note = "ok"
    if not deps.get("ipfs_configured"):
        ipfs_note = "NOT configured (check IPFS_API_URL in backend/.env)"
    else:
        try:
            ipfs_url = os.environ.get(
                "SMOKE_IPFS_API_URL", APP.config.get("IPFS_API_URL", IPFS_API_URL),
            )
            requests.post(f"{ipfs_url.rstrip('/')}/api/v0/version", timeout=3)
        except requests.RequestException:
            ipfs_note = "NOT reachable - start it:  ipfs daemon   (M2.1 will fail without it)"

    print(f"  service   : {payload.get('service')} v{payload.get('version')} "
          f"({payload.get('environment')})")
    print(f"  database  : {'configured' if deps.get('database_configured') else 'MISSING'}")
    print(f"  rpc + all 5 contracts : "
          f"{'configured' if not any('contract' in p for p in problems) else 'MISSING'}")
    print(f"  ipfs      : {ipfs_note}")
    return problems


# ---------------------------------------------------------------------------
# Module 1 - Zero-Proxy Attendance
# ---------------------------------------------------------------------------
def m1_enroll_face() -> str:
    status, payload = api(
        "POST",
        "/student/enroll-face",
        token=AUTH["student_token"],
        json={"name": STUDENT_NAME, "face_image": ENROLL_IMAGE},
    )
    assert status == 201, f"enroll-face returned {status}: {payload}"
    student = payload["student"]
    assert student["id"] == STUDENT_ID, f"unexpected student id: {student}"
    assert student["has_face_embedding"] is True, "face embedding was not stored"
    embedding = payload["embedding"]
    return (f"student {STUDENT_ID} ({STUDENT_NAME!r}) enrolled via "
            f"{embedding['provider']} ({embedding['dims']}-d), created={payload['created']}")


def m1_register_device() -> str:
    status, payload = api(
        "POST",
        "/student/register-device",
        token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    if status == 409 and payload.get("error") == "device_already_bound":
        # Preserve the existing rebind recovery check, scoped to our own student.
        # Unique accounts/devices mean a normal fresh run never needs this path.
        status, payload = api(
            "POST",
            "/admin/rebind-device",
            token=AUTH["hod_token"],
            json={"student_id": STUDENT_ID, "device_id": DEVICE_ID},
        )
    assert status == 200, f"register-device returned {status}: {payload}"
    assert payload["student"]["device_id"] == DEVICE_ID, f"unexpected binding: {payload}"
    return f"device {DEVICE_ID!r} bound (already_bound={payload.get('already_bound')})"


def m1_verify_identity() -> str:
    status, payload = api(
        "POST",
        "/attendance/verify-identity",
        token=AUTH["student_token"],
        json={"face_image": CAPTURE_IMAGE},
    )
    assert status == 200, f"verify-identity returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    assert payload["similarity"] >= payload["threshold"], (
        f"similarity {payload['similarity']} below threshold {payload['threshold']}"
    )
    STATE["token"] = token
    return (f"method={payload['method']} similarity={payload['similarity']} "
            f"(threshold {payload['threshold']}), token valid {payload['expires_in']}s")


def m1_start_session() -> str:
    global ATTENDANCE_CLASS
    ATTENDANCE_CLASS = seed_class(COURSES[0])
    status, payload = api(
        "POST", "/session/start", token=AUTH["professor_token"],
        json={"class_id": ATTENDANCE_CLASS},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    session = payload["session"]
    assert session["is_active"] is True, f"new session is not active: {session}"
    STATE["attendance_session_id"] = session["id"]
    return (f"session #{session['id']} for class {ATTENDANCE_CLASS!r} is live, "
            f"nonce rotates every {payload['rotation_seconds']}s")


def m1_valid_scan() -> str:
    session_id = STATE["attendance_session_id"]
    status, payload = _scan_with_live_nonce(
        lambda nonce: attendance_scan(STATE["token"], DEVICE_ID, session_id, nonce),
        session_id,
    )
    assert status == 200, f"valid scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    tx = note_tx(
        f"M1 attendance lock - student {STUDENT_ID}, session #{session_id}",
        payload["transaction_hash"],
    )
    return f"locked on-chain in block {payload['block_number']}: {tx}"


def m1_duplicate_scan_rejected() -> str:
    # Fresh token + fresh nonce so the scan reliably reaches the on-chain
    # duplicate check instead of failing earlier.
    status, payload = api(
        "POST",
        "/attendance/verify-identity",
        token=AUTH["student_token"],
        json={"face_image": CAPTURE_IMAGE},
    )
    assert status == 200, f"verify-identity returned {status}: {payload}"
    session_id = STATE["attendance_session_id"]
    status, payload = _scan_with_live_nonce(
        lambda nonce: attendance_scan(payload["verification_token"], DEVICE_ID, session_id, nonce),
        session_id,
    )
    assert status == 409, f"duplicate scan must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "attendance_already_locked", f"unexpected error: {payload}"
    assert payload.get("check") == "onchain_lock", f"unexpected check: {payload}"
    return f"rejected at check '{payload['check']}': {payload['message']}"


def m1_numeric_code_scan() -> str:
    """A second student checks in by typing the projector's 6-digit code."""
    global STUDENT2_ROW_OWNED

    session_id = STATE["attendance_session_id"]
    # register-device also creates the Student row for a fresh registration
    # account (Phase 1.7c), so nothing is pre-seeded for this student.
    status, payload = api(
        "POST", "/student/register-device", token=AUTH["student2_token"],
        json={"device_id": DEVICE2_ID},
    )
    assert status == 200, f"second student register-device returned {status}: {payload}"
    assert payload["student"]["device_id"] == DEVICE2_ID, f"unexpected binding: {payload}"
    STUDENT2_ROW_OWNED = True

    status, payload = api(
        "POST", "/students/device/enroll", token=AUTH["student2_token"],
        json={"device_id": DEVICE2_ID},
    )
    assert status == 201, f"second student device/enroll returned {status}: {payload}"
    secret = payload.get("device_secret")
    assert isinstance(secret, str) and secret, f"no device secret in response: {payload}"
    STATE["device2_secret"] = secret
    token = device_identity_token(AUTH["student2_token"], DEVICE2_ID, secret)

    def scan_with_current_code() -> tuple[str, tuple[int, dict]]:
        status, qr = api("GET", f"/session/{session_id}/qr", token=AUTH["professor_token"])
        assert status == 200, f"GET /session/{session_id}/qr returned {status}: {qr}"
        code = str(qr.get("numeric_code") or "")
        assert re.fullmatch(r"\d{6}", code), f"numeric_code is not 6 digits: {qr}"
        return code, attendance_scan_code(
            token, DEVICE2_ID, session_id, code, login_token=AUTH["student2_token"],
        )

    code, (status, payload) = scan_with_current_code()
    if payload.get("check") == "session_nonce":  # the code rotated between read and scan
        code, (status, payload) = scan_with_current_code()
    assert status == 200, f"numeric-code scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    tx = note_tx(
        f"M1 numeric-code check-in - student {STUDENT2_ID}, session #{session_id}",
        payload["transaction_hash"],
    )
    return (f"code {code} from GET /session/{session_id}/qr accepted for the second "
            f"student {STUDENT2_ID}, session #{session_id} locked: {tx}")


def m1_socket_checked_in() -> str:
    """The conductor's socket receives student_checked_in for a live check-in."""
    class_id = seed_class(SOCKET_COURSE)
    STATE["socket_class_id"] = class_id
    status, payload = api(
        "POST", "/session/start", token=AUTH["professor_token"],
        json={"class_id": class_id},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    session_id = payload["session"]["id"]
    STATE["socket_session_id"] = session_id

    client = LiveSocket(AUTH["professor_token"])
    try:
        client.connect()
        client.emit("join_session", {"session_id": session_id})
        event, reply = client.wait_for(("joined", "join_error"))
        assert event == "joined", f"the conductor was refused: {reply}"
        assert reply.get("session_id") == session_id, f"joined another session: {reply}"

        token = device_identity_token(
            AUTH["student2_token"], DEVICE2_ID, STATE["device2_secret"],
        )
        status, payload = _scan_with_live_nonce(
            lambda nonce: attendance_scan(
                token, DEVICE2_ID, session_id, nonce, login_token=AUTH["student2_token"],
            ),
            session_id,
        )
        assert status == 200, f"live scan was rejected: {status} {payload}"
        tx = note_tx(
            f"M1 socket check-in - student {STUDENT2_ID}, session #{session_id}",
            payload["transaction_hash"],
        )

        _event, checked_in = client.wait_for("student_checked_in", timeout=15)
    finally:
        client.close()

    assert checked_in.get("student_id") == STUDENT2_ID, (
        f"event names student {checked_in.get('student_id')}, expected {STUDENT2_ID}: "
        f"{checked_in}"
    )
    assert checked_in.get("count", 0) >= 1, f"event count is wrong: {checked_in}"
    assert checked_in.get("checked_in_at"), f"event has no check-in time: {checked_in}"
    return (f"joined 'session:{session_id}' as the conductor; student_checked_in arrived "
            f"for {checked_in.get('full_name')!r} (count={checked_in.get('count')}): {tx}")


def m1_export_includes_checkin() -> str:
    """The class's monthly xlsx parses and contains the checked-in student."""
    class_id = STATE["socket_class_id"]
    month = export_month()
    status, content, headers = api_download(
        f"/attendance/export?class_id={class_id}&month={month}&format=xlsx",
        token=AUTH["professor_token"],
    )
    assert status == 200, f"attendance/export returned {status}: {content[:300]!r}"
    disposition = headers.get("Content-Disposition", "")
    assert ".xlsx" in disposition, f"export is not an xlsx attachment: {disposition!r}"

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True)
    assert "Attendance" in workbook.sheetnames, f"no Attendance sheet: {workbook.sheetnames}"
    rows = list(workbook["Attendance"].iter_rows(values_only=True))
    assert rows, "the export has no rows at all"
    header = [str(cell) for cell in rows[0] if cell is not None]
    assert "Course code" in header, f"unexpected export header: {header}"
    matching = [
        row for row in rows[1:]
        if STUDENT2_ROLL in [str(cell) for cell in row if cell is not None]
    ]
    assert matching, (
        f"student {STUDENT2_ROLL} is missing from the {month} export of class "
        f"#{class_id} ({len(rows) - 1} data row(s))"
    )
    values = [str(cell) for cell in matching[0] if cell is not None]
    assert SOCKET_COURSE in values, f"the exported row is for another class: {values}"
    return (f"{month} xlsx for class #{class_id} parsed: {len(rows) - 1} row(s), "
            f"including {STUDENT2_ROLL} in {SOCKET_COURSE}")


def m1_end_session_rejects_scan() -> str:
    """Ending the session stops check-ins: a later scan fails at check 'session'."""
    session_id = STATE["socket_session_id"]
    status, payload = api(
        "POST", f"/session/{session_id}/end", token=AUTH["professor_token"], json={},
    )
    assert status == 200, f"session/end returned {status}: {payload}"
    session = payload.get("session") or {}
    assert session.get("is_active") is False, f"session is still active: {session}"
    assert payload.get("ended_at"), f"no ended_at in response: {payload}"
    count = payload.get("count")
    assert isinstance(count, int) and count >= 1, (
        f"the ended session lost its check-ins: {payload}"
    )

    status, identity = api(
        "POST", "/attendance/verify-identity", token=AUTH["student_token"],
        json={"face_image": CAPTURE_IMAGE},
    )
    assert status == 200, f"verify-identity returned {status}: {identity}"
    status, payload = attendance_scan(
        identity["verification_token"], DEVICE_ID, session_id, "0" * 32,
    )
    assert status == 409, f"a scan after the end must be 409, got {status}: {payload}"
    assert payload.get("check") == "session", f"unexpected check: {payload}"
    assert payload.get("error") == "session_ended", f"unexpected error: {payload}"
    return (f"session #{session_id} ended with {count} check-in(s); the next scan was "
            f"refused at check 'session': {payload['message']}")


# ---------------------------------------------------------------------------
# Module 2 - Multi-Sig Audit Trail
# ---------------------------------------------------------------------------
def m2_upload_evidence() -> str:
    status, payload = api(
        "POST",
        "/ipfs/upload",
        token=AUTH["professor_token"],
        files={"file": (f"smoke-evidence-{RUN_ID}.png", EVIDENCE_PNG, "image/png")},
    )
    # Track the resource before assertions so even a bad response is cleaned up.
    if status == 201:
        STATE["cid"] = payload.get("cid")
    assert status == 201, f"ipfs/upload returned {status}: {payload}"
    cid = payload.get("cid", "")
    assert CID_PATTERN.match(cid), f"not a valid CID in response: {payload}"
    assert payload.get("gateway_url") == f"https://ipfs.io/ipfs/{cid}", f"bad gateway URL: {payload}"
    STATE["cid"] = cid
    return f"evidence pinned, cid={cid}"


def m2_propose() -> str:
    status, payload = api(
        "POST",
        "/records/propose",
        token=AUTH["professor_token"],
        json={
            "studentId": STUDENT_ID,
            "field": "grade",
            "oldValue": "B",
            "newValue": "A",
            "ipfsCid": STATE["cid"],
        },
    )
    assert status == 201, f"propose returned {status}: {payload}"
    assert payload.get("status") == "Pending", f"new proposal is not Pending: {payload}"
    proposal_id = payload.get("onchain_proposal_id")
    assert isinstance(proposal_id, int) and proposal_id >= 1, f"bad onchain_proposal_id: {payload}"
    STATE["proposal_id"] = proposal_id

    # The HOD dashboard feed must see it while it is open.
    pending_status, pending_payload = api("GET", "/records/pending", token=AUTH["hod_token"])
    assert pending_status == 200, f"GET /records/pending returned {pending_status}: {pending_payload}"
    pending_ids = [row.get("onchainProposalId") for row in pending_payload.get("pending") or []]
    assert proposal_id in pending_ids, f"proposal {proposal_id} not in the pending list: {pending_ids}"

    tx = note_tx(f"M2 record change proposed - proposal {proposal_id} (grade B -> A)",
                 payload["transaction_hash"])
    return f"proposal {proposal_id} Pending with 1 signature, visible to HODs: {tx}"


def m2_approve_hod() -> str:
    status, payload = api(
        "POST", f"/records/{STATE['proposal_id']}/approve",
        token=AUTH["hod_token"], json={},
    )
    assert status == 200, f"approve returned {status}: {payload}"
    assert payload.get("status") == "Executed", f"proposal not Executed: {payload}"
    assert payload.get("signature_count") == 2, f"expected 2 signatures: {payload}"
    assert payload.get("record_updated") is True, f"RecordUpdated did not fire: {payload}"
    tx = note_tx(f"M2 record change executed - proposal {STATE['proposal_id']} (HOD co-sign)",
                 payload["transaction_hash"])
    return (f"Executed with {payload['signature_count']} signatures (record_updated=True): {tx}")


def m2_pending_excludes() -> str:
    status, payload = api("GET", "/records/pending", token=AUTH["hod_token"])
    assert status == 200, f"GET /records/pending returned {status}: {payload}"
    rows = payload.get("pending") or []
    ids = [row.get("onchainProposalId") for row in rows]
    assert STATE["proposal_id"] not in ids, (
        f"proposal {STATE['proposal_id']} is still pending: {ids}"
    )
    return f"proposal {STATE['proposal_id']} left the pending list ({len(ids)} open proposal(s) remain)"


def temp_admin_token() -> str:
    """Log in a throwaway ADMIN row for the D2 'ADMIN cannot approve' rule.

    ADMIN accounts only come from the bootstrap endpoint, which this script
    never calls (that lock is permanent and belongs to the demo admin), so the
    fixture is written directly here and removed by cleanup().
    """
    global TEMP_ADMIN_CREATED
    from app.extensions import db
    from app.models import User, UserRole
    from app.services.auth import hash_password

    with APP.app_context():
        if User.query.filter_by(email=TEMP_ADMIN_EMAIL).first() is None:
            db.session.add(User(
                role=UserRole.ADMIN,
                email=TEMP_ADMIN_EMAIL,
                password_hash=hash_password(PASSWORD),
                full_name="Smoke Test Admin",
                department="Computer Science",
                is_active=True,
            ))
            db.session.commit()
            TEMP_ADMIN_CREATED = True
    status, payload = api(
        "POST", "/auth/login", json={"email": TEMP_ADMIN_EMAIL, "password": PASSWORD},
    )
    assert status == 200, f"temporary ADMIN login failed ({status}): {payload}"
    assert payload["user"]["role"] == "ADMIN", f"the fixture is not an ADMIN: {payload}"
    return payload["token"]


def m2_grade_correction() -> str:
    """Propose -> the Grade row locks -> HOD approves -> value changes, lock clears."""
    from app.extensions import db
    from app.models import Grade

    with APP.app_context():
        db.session.add(Grade(
            student_id=STUDENT_ID, course_code=GRADE_COURSE, grade_value=GRADE_OLD,
        ))
        db.session.commit()

    status, payload = api(
        "POST", "/grades/corrections", token=AUTH["professor_token"],
        json={
            "student_id": STUDENT_ID,
            "course_code": GRADE_COURSE,
            "new_grade": GRADE_NEW,
            "justification": GRADE_JUSTIFICATION,
        },
    )
    assert status == 201, f"grades/corrections returned {status}: {payload}"
    proposal_id = payload.get("onchain_proposal_id")
    assert isinstance(proposal_id, int) and proposal_id >= 1, (
        f"bad onchain_proposal_id: {payload}"
    )
    assert payload.get("status") == "Pending", f"the correction is not Pending: {payload}"
    locked_view = payload.get("grade") or {}
    assert locked_view.get("locked") is True, f"the grade row was not locked: {payload}"
    assert locked_view.get("lockedByProposalId"), f"no lock owner: {payload}"
    STATE["grade_proposal_id"] = proposal_id
    if payload.get("ipfs_cid"):
        STATE["extra_cids"].append(payload["ipfs_cid"])
    propose_tx = note_tx(
        f"M2 grade correction proposed - {GRADE_COURSE} {GRADE_OLD} -> {GRADE_NEW}",
        payload["transaction_hash"],
    )

    # The lock lives in the DB, so read it there as well as through the API.
    with APP.app_context():
        row = Grade.query.filter_by(student_id=STUDENT_ID, course_code=GRADE_COURSE).first()
        assert row is not None and row.locked_by_proposal_id is not None, (
            "the grade row is not locked in the DB"
        )

    status, payload = api(
        "POST", f"/grades/corrections/{proposal_id}/approve",
        token=AUTH["hod_token"], json={},
    )
    assert status == 200, f"HOD approve returned {status}: {payload}"
    assert payload.get("status") == "Executed", f"the correction did not execute: {payload}"
    approved_view = payload.get("grade") or {}
    assert approved_view.get("gradeValue") == GRADE_NEW, (
        f"the grade value did not change: {payload}"
    )
    assert approved_view.get("locked") is False, f"the lock was not cleared: {payload}"
    approve_tx = note_tx(
        f"M2 grade correction approved - {GRADE_COURSE} is now {GRADE_NEW}",
        payload["transaction_hash"],
    )

    status, payload = api("GET", f"/grades/{STUDENT_ID}", token=AUTH["student_token"])
    assert status == 200, f"GET /grades/{STUDENT_ID} returned {status}: {payload}"
    row_data = next(
        (item for item in payload.get("grades") or []
         if item.get("courseCode") == GRADE_COURSE),
        None,
    )
    assert row_data is not None, (
        f"{GRADE_COURSE} is missing from the student's grades: {payload}"
    )
    assert row_data.get("gradeValue") == GRADE_NEW and row_data.get("locked") is False, (
        f"the student view is stale: {row_data}"
    )
    return (f"{GRADE_COURSE}: {GRADE_OLD} -> {GRADE_NEW}, row locked while pending then "
            f"unlocked; {propose_tx} / {approve_tx}")


def m2_admin_cannot_approve() -> str:
    """D2: a second correction stays pending; the ADMIN attempt is refused."""
    from app.models import Grade

    admin_token = temp_admin_token()
    status, payload = api(
        "POST", "/grades/corrections", token=AUTH["professor_token"],
        json={
            "student_id": STUDENT_ID,
            "course_code": GRADE_COURSE,
            "new_grade": GRADE_SECOND,
            "justification": GRADE_JUSTIFICATION,
        },
    )
    assert status == 201, f"the second correction returned {status}: {payload}"
    proposal_id = payload.get("onchain_proposal_id")
    assert isinstance(proposal_id, int) and proposal_id >= 1, (
        f"bad onchain_proposal_id: {payload}"
    )
    assert (payload.get("grade") or {}).get("locked") is True, (
        f"the second correction did not lock the row: {payload}"
    )
    if payload.get("ipfs_cid"):
        STATE["extra_cids"].append(payload["ipfs_cid"])

    status, payload = api(
        "POST", f"/grades/corrections/{proposal_id}/approve",
        token=admin_token, json={},
    )
    assert status == 403, f"an ADMIN approve must be 403, got {status}: {payload}"
    assert payload.get("check") == "auth", f"unexpected check: {payload}"
    assert payload.get("error") == "insufficient_role", f"unexpected error: {payload}"

    # The refused call must not have touched the pending correction.
    with APP.app_context():
        row = Grade.query.filter_by(student_id=STUDENT_ID, course_code=GRADE_COURSE).first()
        assert row is not None and row.locked_by_proposal_id is not None, (
            "the refused approval cleared the lock"
        )
        assert row.grade_value == GRADE_NEW, (
            f"the refused approval changed the grade to {row.grade_value!r}"
        )
    return (f"ADMIN was refused at check 'auth' (403) for correction {proposal_id}; "
            f"{GRADE_COURSE} stayed {GRADE_NEW} and locked for the HOD")


# ---------------------------------------------------------------------------
# Module 3 - Faculty Workload Ledger
# ---------------------------------------------------------------------------
def m3_seed_classes() -> str:
    created = [seed_class(course) for course in COURSES[1:]]
    STATE["conducted_class_id"], STATE["substitute_class_id"] = created
    return (f"classes #{created[0]} and #{created[1]} seeded for faculty {PROFESSOR_ID}, "
            "start=UTC now - 5s (seeded demo roster untouched)")


def m3_professor_starts() -> str:
    status, payload = api(
        "POST",
        "/faculty/session/start",
        token=AUTH["professor_token"],
        json={"class_id": STATE["conducted_class_id"]},
    )
    assert status == 201, f"faculty session/start returned {status}: {payload}"
    conductor = (payload.get("workload_session") or {}).get("conductorId")
    assert conductor == PROFESSOR_ID, f"workload session conductor is {conductor}: {payload}"
    session = payload.get("session") or {}
    assert session.get("is_active") is True, f"attendance session not active: {session}"
    STATE["faculty_session_id"] = session["id"]
    return (f"live session #{session['id']} for class #{STATE['conducted_class_id']}, "
            f"conductor=Prof. {conductor}, QR at {payload.get('qr_poll_url')}")


def m3_scan_conducted() -> str:
    session_id = STATE["faculty_session_id"]
    status, payload = _scan_with_live_nonce(
        lambda nonce: faculty_scan(session_id, nonce), session_id
    )
    assert status == 201, f"faculty scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected status: {payload}"
    session_log = payload.get("session_log") or {}
    assert session_log.get("conductedBy") == PROFESSOR_ID, (
        f"workload credited to {session_log.get('conductedBy')}, not Prof. {PROFESSOR_ID}: {payload}"
    )
    tx = note_tx(
        f"M3 class #{STATE['conducted_class_id']} CONDUCTED - credited to Prof. {PROFESSOR_ID}",
        payload["transaction_hash"],
    )
    return f"class #{STATE['conducted_class_id']} CONDUCTED on-chain, credit Prof. {PROFESSOR_ID}: {tx}"


def m3_issue_substitute_key() -> str:
    status, payload = api(
        "POST",
        "/faculty/substitute/issue",
        token=AUTH["professor_token"],
        json={
            "class_id": STATE["substitute_class_id"],
            "substitute_id": SUBSTITUTE_ID,
        },
    )
    assert status == 201, f"substitute/issue returned {status}: {payload}"
    token = payload.get("substitute_token")
    assert token, f"no substitute_token in response: {payload}"
    assert payload.get("substitute_id") == SUBSTITUTE_ID, f"wrong substitute echoed: {payload}"
    STATE["substitute_token"] = token
    return (f"30-min key minted for Prof. {SUBSTITUTE_ID} (expires {payload.get('expires_at')}): "
            f"{token[:32]}...")


def m3_substitute_redeems() -> str:
    status, payload = api(
        "POST",
        "/faculty/substitute/redeem",
        token=AUTH["substitute_token"],
        json={"token": STATE["substitute_token"]},
    )
    assert status == 201, f"substitute/redeem returned {status}: {payload}"
    assert payload.get("status") == "redeemed", f"unexpected status: {payload}"
    conductor = (payload.get("workload_session") or {}).get("conductorId")
    assert conductor == SUBSTITUTE_ID, f"redeemed session conductor is {conductor}: {payload}"
    session = payload.get("session") or {}
    STATE["substitute_session_id"] = session["id"]
    return (f"redeemed - Prof. {SUBSTITUTE_ID} now conducts class "
            f"#{STATE['substitute_class_id']} (live session #{session['id']})")


def m3_scan_credits_primary() -> str:
    session_id = STATE["substitute_session_id"]
    status, payload = _scan_with_live_nonce(
        lambda nonce: faculty_scan(session_id, nonce), session_id,
        token=AUTH["substitute_token"],
    )
    assert status == 201, f"faculty scan returned {status}: {payload}"
    assert payload.get("status") == "conducted", f"unexpected status: {payload}"
    session_log = payload.get("session_log") or {}
    # D1 deliberately replaces M3.6's old substitute-credit expectation.
    assert session_log.get("professorId") == PROFESSOR_ID, (
        f"workload credited to {session_log.get('professorId')}, not primary "
        f"Prof. {PROFESSOR_ID}: {payload}"
    )
    assert session_log.get("conductedBy") == SUBSTITUTE_ID, (
        f"conductor is {session_log.get('conductedBy')}, not substitute "
        f"Prof. {SUBSTITUTE_ID}: {payload}"
    )
    from app.services import workload

    with APP.app_context():
        onchain = workload.get_workload_log(STATE["substitute_class_id"])
    assert onchain["logged"] is True and onchain["status"] == "CONDUCTED", f"on-chain state wrong: {onchain}"
    assert onchain["professor_id"] == PROFESSOR_ID, f"on-chain primary credit wrong: {onchain}"
    tx = note_tx(
        f"M3 class #{STATE['substitute_class_id']} CONDUCTED - credited to primary Prof. {PROFESSOR_ID}",
        payload["transaction_hash"],
    )
    return (f"class #{STATE['substitute_class_id']} CONDUCTED on-chain, "
            f"credit primary Prof. {PROFESSOR_ID}, conductor substitute Prof. {SUBSTITUTE_ID}: {tx}")


def m3_logs_show_both() -> str:
    status, payload = api("GET", "/faculty/logs", token=AUTH["hod_token"])
    assert status == 200, f"GET /faculty/logs returned {status}: {payload}"
    entries = {entry.get("classId"): entry for entry in payload.get("logs") or []}
    expected = [
        (STATE["conducted_class_id"], PROFESSOR_ID),
        (STATE["substitute_class_id"], SUBSTITUTE_ID),
    ]
    for class_id, conductor_id in expected:
        entry = entries.get(class_id)
        assert entry is not None, f"class #{class_id} missing from /faculty/logs: {payload}"
        assert entry.get("conductedBy") == conductor_id, (
            f"class #{class_id} credited to {entry.get('conductedBy')}, "
            f"expected Prof. {conductor_id}: {entry}"
        )
        assert str(entry.get("onchainTxHash", "")).startswith("0x"), f"no tx hash: {entry}"
    return (f"classes #{STATE['conducted_class_id']} (Prof. {PROFESSOR_ID}) and "
            f"#{STATE['substitute_class_id']} (substitute Prof. {SUBSTITUTE_ID}) both in the "
            f"admin audit-log feed ({payload.get('count')} entries total)")


# ---------------------------------------------------------------------------
# Module 4 - Device-Bound Biometric Credential (D4)
# ---------------------------------------------------------------------------
def m4_device_credential() -> str:
    """Enroll the device secret, then verify a correct and a wrong HMAC."""
    status, payload = api(
        "POST", "/students/device/enroll", token=AUTH["student_token"],
        json={"device_id": DEVICE_ID},
    )
    assert status == 201, f"students/device/enroll returned {status}: {payload}"
    secret = payload.get("device_secret")
    assert isinstance(secret, str) and len(secret) >= 32, f"no device secret: {payload}"
    assert payload.get("device_id") == DEVICE_ID, f"wrong device echoed: {payload}"
    assert payload.get("algorithm") == "HMAC-SHA256", f"unexpected algorithm: {payload}"

    challenge = device_challenge(AUTH["student_token"])
    response = device_response(secret, challenge, DEVICE_ID)
    status, payload = verify_device(AUTH["student_token"], DEVICE_ID, challenge, response)
    assert status == 200, f"verify-identity-device returned {status}: {payload}"
    token = payload.get("verification_token")
    assert isinstance(token, str) and token, f"no verification_token: {payload}"
    assert payload.get("method") == "device-biometric", f"unexpected method: {payload}"
    expires_in = int(payload.get("expires_in") or 0)
    assert 0 < expires_in <= 60, f"unexpected token TTL: {payload}"

    # A wrong HMAC spends a fresh challenge and must not mint a token.
    challenge = device_challenge(AUTH["student_token"])
    status, payload = verify_device(AUTH["student_token"], DEVICE_ID, challenge, "00" * 32)
    assert status == 401, f"a wrong HMAC was not rejected with 401: {status} {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload.get("error") == "bad_response", f"unexpected error: {payload}"
    return (f"secret enrolled for device {DEVICE_ID!r}, correct HMAC -> a {expires_in}s "
            f"device-biometric token; wrong HMAC refused at check 'identity'")


# ---------------------------------------------------------------------------
# Cleanup - only this run's local fixtures, never table truncation or sequence reset
# ---------------------------------------------------------------------------
def cleanup() -> str:
    errors = []
    removed = 0
    if APP is not None and ACCOUNTS:
        from app.extensions import db
        from app.models import (
            AttendanceSession, FacultyProfile, Grade, InstitutionWhitelist, Proposal,
            ScheduledClass, SessionLog, Student, StudentProfile, SubstituteToken,
            User, WorkloadSession,
        )

        try:
            with APP.app_context():
                # Resolve by unique emails too: registration may have committed
                # even if an HTTP response or a subsequent assertion failed.
                emails = [account["email"] for account in ACCOUNTS.values()]
                if TEMP_ADMIN_CREATED:
                    emails.append(TEMP_ADMIN_EMAIL)
                user_ids = [user.id for user in User.query.filter(User.email.in_(emails)).all()]
                class_ids = [row.id for row in ScheduledClass.query.filter(
                    ScheduledClass.professor_id.in_(user_ids),
                    ScheduledClass.course_code.in_(COURSES + [SOCKET_COURSE]),
                ).all()]
                session_names = [str(class_id) for class_id in class_ids]
                session_names += [f"workload-{class_id}" for class_id in class_ids]
                # Delete children before parents. All filters are this run only.
                for model in (SessionLog, SubstituteToken, WorkloadSession):
                    removed += model.query.filter(model.class_id.in_(class_ids)).delete(
                        synchronize_session=False,
                    )
                # Full cold resets preserve old AttendanceSession rows. Do not
                # delete older rows whose class names happened to be re-issued.
                removed += AttendanceSession.query.filter(
                    AttendanceSession.class_id.in_(session_names),
                    AttendanceSession.start_time >= RUN_STARTED,
                ).delete(synchronize_session=False)
                removed += ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).delete(
                    synchronize_session=False,
                )
                # This recovers a proposal whose HTTP response was lost, too.
                student_id = AUTH.get("student_id")
                if student_id is not None:
                    removed += Proposal.query.filter_by(student_id=student_id).delete(
                        synchronize_session=False,
                    )
                    # M2.5/M2.6 seed this run's grade row; nothing references it
                    # once its correction mirror is gone.
                    removed += Grade.query.filter(
                        Grade.student_id == student_id,
                        Grade.course_code == GRADE_COURSE,
                    ).delete(synchronize_session=False)
                if STUDENT_ROW_OWNED:
                    removed += Student.query.filter_by(id=STUDENT_ID).delete(
                        synchronize_session=False,
                    )
                if STUDENT2_ROW_OWNED:
                    removed += Student.query.filter_by(id=STUDENT2_ID).delete(
                        synchronize_session=False,
                    )
                for model in (FacultyProfile, StudentProfile):
                    removed += model.query.filter(model.user_id.in_(user_ids)).delete(
                        synchronize_session=False,
                    )
                removed += User.query.filter(User.email.in_(emails)).delete(
                    synchronize_session=False,
                )
                hod = ACCOUNTS["hod"]
                removed += InstitutionWhitelist.query.filter_by(
                    employee_id=hod["employee_id"], email=hod["email"],
                ).delete(synchronize_session=False)
                assert User.query.filter(User.email.in_(emails)).count() == 0
                assert ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).count() == 0
                assert AttendanceSession.query.filter(
                    AttendanceSession.class_id.in_(session_names),
                    AttendanceSession.start_time >= RUN_STARTED,
                ).count() == 0
                db.session.commit()
        except Exception as exc:  # cleanup failure must fail the run, not hide it
            errors.append(f"DB cleanup failed: {_reason(exc)}")

    cids = [cid for cid in [STATE.get("cid"), *STATE.get("extra_cids", [])] if cid]
    unpinned = 0
    for cid in cids:
        try:
            assert CID_PATTERN.fullmatch(str(cid)), f"unsafe CID: {cid!r}"
            # Kubo is a local service, not the Flask API: no API bearer token here.
            ipfs_url = APP.config["IPFS_API_URL"].rstrip("/")
            response = requests.post(
                f"{ipfs_url}/api/v0/pin/rm", params={"arg": cid}, timeout=10,
            )
            assert response.status_code == 200, (
                f"Kubo pin/rm returned {response.status_code}: {response.text[:200]}"
            )
            assert cid in response.json().get("Pins", []), "Kubo did not confirm unpinning"
            unpinned += 1
        except Exception as exc:
            errors.append(
                f"IPFS pin {cid} not removed: {_reason(exc)}; restore local Kubo or "
                "remove this run's external fallback pin manually"
            )
    assert not errors, "; ".join(errors)
    return (f"removed {removed} local rows; "
            f"{unpinned}/{len(cids)} local IPFS pin(s) removed; "
            "DB sequences and permanent on-chain history unchanged")


# ---------------------------------------------------------------------------
# Summary + main
# ---------------------------------------------------------------------------
def _dotted(text: str, width: int = 66) -> str:
    return (text + " ").ljust(width, ".")


def print_summary() -> None:
    rule = "=" * 78
    print("\n" + rule)
    print(f"  SUMMARY - {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print(rule)

    module_titles = {
        "S": "Setup - Authentication and governance",
        "H": "Health gate",
        "M1": "Module 1 - Zero-Proxy Attendance",
        "M2": "Module 2 - Multi-Sig Audit Trail",
        "M3": "Module 3 - Faculty Workload Ledger",
        "M4": "Module 4 - Device-Bound Biometric Credential",
        "C": "Cleanup - this run's local fixtures",
        "F": "Unexpected runner failure",
    }
    for prefix, title in module_titles.items():
        rows = [row for row in RESULTS if row[0].startswith(prefix)]
        if not rows:
            continue
        passed = sum(1 for row in rows if row[2] == "PASS")
        failed = sum(1 for row in rows if row[2] == "FAIL")
        skipped = sum(1 for row in rows if row[2] == "SKIP")
        tally = f"{passed}/{len(rows)} PASS"
        if failed:
            tally += f", {failed} FAIL"
        if skipped:
            tally += f", {skipped} SKIP"
        print(f"  {title}")
        for key, label, status, _detail in rows:
            print(f"    {status:<4}  {key:<5} {label}")
        print(f"          {_dotted('')} {tally}")

    if TRANSACTIONS:
        print("\n  On-chain transactions anchored during this run:")
        for label, tx in TRANSACTIONS:
            print(f"    {label}")
            print(f"      {tx}")

    failures = [row for row in RESULTS if row[2] == "FAIL"]
    if failures:
        print("\n  Failures (full detail):")
        for key, label, _status, detail in failures:
            print(f"    {key} {label}")
            print(f"      {detail}")

    total = len(RESULTS)
    passed = sum(1 for row in RESULTS if row[2] == "PASS")
    print("\n" + "-" * 78)
    if failures:
        names = ", ".join(f"{row[0]} {row[1]}" for row in failures)
        print(f"  RESULT: FAILED - {passed}/{total} steps passed. Failed: {names}")
        print("          (details under 'Failures' above; fix and re-run)")
    else:
        print(f"  RESULT: PASSED - {passed}/{total} steps, all four modules verified.")
        print("          The stack is demo-ready.")
    print(rule)


def main() -> int:
    print("=" * 78)
    print("  EDULEDGER FULL-STACK SMOKE TEST (Stage 1 - four modules)")
    print(f"  backend: {BASE_URL}")
    print("=" * 78)

    try:
        setup = Module(0, "Authentication and governance")
        setup.step("S0", "register + login faculty, substitute, student and HOD", setup_auth)
        ready = not setup.failed
        if ready:
            print("\n  Health gate (authenticated)")
            problems = health_gate()
            if problems:
                detail = "; ".join(problems)
                RESULTS.append(("H0", "health gate", "FAIL", detail))
                print(f"  FAIL  H0 {detail}")
                setup.failed = True
                ready = False
        setup.step("S1", "GET governance/status -> 200 (read-only)", governance_status)

        # A failed governance read still lets the independent modules run.
        # Shared auth/health failure is the only reason to skip all four.
        module1 = Module(1, "Zero-Proxy Attendance")
        module1.failed = not ready
        module1.step("M1.1", "enroll smoke student face", m1_enroll_face)
        module1.step("M1.2", "register device", m1_register_device)
        module1.step("M1.3", "verify identity -> 30s token", m1_verify_identity)
        module1.step("M1.4", "start attendance session + live nonce", m1_start_session)
        module1.step("M1.5", "valid scan -> on-chain attendance lock", m1_valid_scan)
        module1.step("M1.6", "duplicate scan -> rejected (already locked)", m1_duplicate_scan_rejected)
        module1.step("M1.7", "numeric code scan by a second student", m1_numeric_code_scan)
        module1.step("M1.8", "socket receives student_checked_in", m1_socket_checked_in)
        module1.step("M1.9", "xlsx export contains that check-in", m1_export_includes_checkin)
        module1.step("M1.10", "end session -> later scan refused", m1_end_session_rejects_scan)

        module2 = Module(2, "Multi-Sig Audit Trail")
        module2.failed = not ready
        module2.step("M2.1", "upload evidence to IPFS -> CID", m2_upload_evidence)
        module2.step("M2.2", "propose record change (evidence CID)", m2_propose)
        module2.step("M2.3", "approve as HOD -> Executed on-chain", m2_approve_hod)
        module2.step("M2.4", "pending list no longer includes it", m2_pending_excludes)
        module2.step("M2.5", "grade correction: lock -> HOD approve -> unlock", m2_grade_correction)
        module2.step("M2.6", "ADMIN cannot approve a correction (403)", m2_admin_cannot_approve)

        module3 = Module(3, "Faculty Workload Ledger")
        module3.failed = not ready
        module3.step("M3.1", "seed 2 fresh scheduled classes", m3_seed_classes)
        module3.step("M3.2", "professor starts the live session", m3_professor_starts)
        module3.step("M3.3", "student scan -> CONDUCTED on-chain", m3_scan_conducted)
        module3.step("M3.4", "issue substitute key to real faculty", m3_issue_substitute_key)
        module3.step("M3.5", "substitute redeems key, starts covering", m3_substitute_redeems)
        module3.step("M3.6", "scan -> PRIMARY credit, substitute conductor", m3_scan_credits_primary)
        module3.step("M3.7", "workload audit log shows both entries", m3_logs_show_both)

        module4 = Module(4, "Device-Bound Biometric Credential (D4)")
        module4.failed = not ready
        module4.step("M4.1", "enroll secret, correct HMAC -> token, wrong HMAC -> 401",
                     m4_device_credential)
    except Exception as exc:
        detail = _reason(exc)
        RESULTS.append(("F0", "unexpected runner failure", "FAIL", detail))
        print(f"  FAIL  F0 {detail}")
    finally:
        Module(5, "Cleanup").step("C1", "remove own fixtures; preserve ID sequences", cleanup)

    print_summary()
    return 1 if any(row[2] != "PASS" for row in RESULTS) else 0


if __name__ == "__main__":
    sys.exit(main())
