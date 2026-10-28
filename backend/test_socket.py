"""End-to-end test of the Phase 1.2a live attendance WebSocket.

Run it against a live stack (from backend/):

    .\\venv\\Scripts\\python.exe test_socket.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - AttendanceLedger deployed   (contracts/: npm run deploy:attendance)
    - Redis running               (with REDIS_URL set, every live event is
      routed through the SocketIO message queue, so Redis must be up)
    - Flask backend running       (backend/: python run.py - socketio.run,
      so the WebSocket shares port 5000), same backend/.env as this script

Steps:
     0. preflight            - GET /health
     1. auth setup           - register + log in a unique conductor, a second
                               faculty, and two students; create a class
                               owned by the conductor
     2. students ready       - enroll-face + register-device for both
     3. session/start        - the conductor opens the live session
     4. connect (no token)   - the socket connection is refused
     5. conductor joins      - "joined" for session:<id>
     6. student joins        - refused with "join_error"
     7. other faculty joins  - refused with "join_error"
     8. first scan           - QR nonce path -> "student_checked_in" count 1
     9. second scan          - numeric code path -> count 2
    10. GET /attendees       - both students, oldest first
    11. POST /end            - "session_ended" {count: 2}
    12. scan after end       - 409 {check: "session", error: "session_ended"}
    13. student POST /end    - 403 (FACULTY-only route)
    Cleanup removes this run's users, profiles, student rows, check-ins,
    session and class. On-chain history is permanent.
"""

import base64
import io
import os
import queue
import sys
import time
from datetime import datetime, timedelta
from uuid import uuid4

import requests
import socketio as socketio_client
from PIL import Image
from socketio.exceptions import ConnectionError as SocketConnectionError

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
from app.util import utcnow

# Windows consoles often default to cp1252, which cannot print the marks below.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

SERVER_URL = "http://127.0.0.1:5000"
BASE_URL = f"{SERVER_URL}/api/v1"
RUN_ID = uuid4().hex[:10]
PASSWORD = "SocketTestPass123!"
COURSE_CODE = f"TEST-SOCK-{RUN_ID}"

CONDUCTOR_EMAIL = f"faculty.sock.{RUN_ID}@gcu.edu.pk"
OTHER_FACULTY_EMAIL = f"other.sock.{RUN_ID}@gcu.edu.pk"

APP = create_app()
AUTH = {"conductor": None, "other_faculty": None}
STATE = {
    "conductor_id": None,
    "other_faculty_id": None,
    "class_id": None,
    "session_id": None,
    "last_nonce": None,
    "conductor_client": None,  # the LiveClient watching the session room
}
# Two students, each with their own device, roll number and enrolled "face".
STUDENTS = [
    {
        "label": "student-1",
        "email": f"student1.sock.{RUN_ID}@gcu.edu.pk",
        "full_name": "Socket Student One",
        "roll_number": f"ROLL-SOCK1-{RUN_ID}",
        "device_id": f"device_sock1_{RUN_ID}",
        "shift": 0,
        "token": None,
        "id": None,
        "row_owned": False,
    },
    {
        "label": "student-2",
        "email": f"student2.sock.{RUN_ID}@gcu.edu.pk",
        "full_name": "Socket Student Two",
        "roll_number": f"ROLL-SOCK2-{RUN_ID}",
        "device_id": f"device_sock2_{RUN_ID}",
        "shift": 40,
        "token": None,
        "id": None,
        "row_owned": False,
    },
]
CLIENTS = []  # every LiveClient opened, so cleanup can close them


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


def enroll_image(student: dict) -> str:
    return _png_data_uri(_gradient_image(shift=student["shift"]))


def capture_image(student: dict) -> str:
    return _png_data_uri(_gradient_image(shift=student["shift"] + 2))


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


def register_and_login(path: str, body: dict, email: str) -> tuple[str, int]:
    status, payload = api("POST", path, json=body)
    assert status == 201, f"{path} returned {status}: {payload}"
    status, payload = api("POST", "/auth/login", json={"email": email, "password": PASSWORD})
    assert status == 200, f"login for {email} failed ({status}): {payload}"
    return payload["token"], payload["user"]["id"]


def get_qr() -> dict:
    """The projector payload, fetched with the conductor's token."""
    status, payload = api("GET", f"/session/{STATE['session_id']}/qr", token=AUTH["conductor"])
    assert status == 200, f"GET /session/{STATE['session_id']}/qr returned {status}: {payload}"
    STATE["last_nonce"] = payload["current_nonce"]
    return payload


def verification_token(student: dict) -> str:
    """A freshly minted (30 s) verification token for one student."""
    status, payload = api(
        "POST", "/attendance/verify-identity", token=student["token"],
        json={"face_image": capture_image(student)},
    )
    assert status == 200, f"{student['label']} verify-identity returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    return token


def scan(student: dict, *, use_code: bool, nonce: str | None = None) -> tuple[int, dict]:
    """POST /attendance/scan for one student, by QR nonce or by numeric code."""
    body = {
        "verification_token": verification_token(student),
        "device_id": student["device_id"],
        "session_id": STATE["session_id"],
    }
    if use_code:
        body["numeric_code"] = get_qr()["numeric_code"]
    else:
        body["nonce"] = nonce if nonce is not None else get_qr()["current_nonce"]
    return api("POST", "/attendance/scan", token=student["token"], json=body)


def scan_live(student: dict, *, use_code: bool) -> tuple[int, dict]:
    """Scan with what the projector shows now, retrying once if it rotated."""
    status, payload = scan(student, use_code=use_code)
    if payload.get("error") in ("code_mismatch", "nonce_mismatch", "nonce_expired"):
        print("   note: QR rotated mid-flight - retrying with the fresh value")
        status, payload = scan(student, use_code=use_code)
    return status, payload


# --------------------------------------------------------------------------
# Socket.IO client
# --------------------------------------------------------------------------
class LiveClient:
    """A socket.io client that records the live attendance events it gets."""

    EVENTS = ("joined", "join_error", "student_checked_in", "session_ended")

    def __init__(self, label: str, token: str | None = None):
        self.label = label
        self.token = token
        self.received = queue.Queue()
        self.sio = socketio_client.Client(reconnection=False)
        for event in self.EVENTS:
            self.sio.on(event, self._recorder(event))
        CLIENTS.append(self)

    def _recorder(self, event: str):
        def handler(payload=None):
            self.received.put((event, payload))

        return handler

    def connect(self) -> None:
        """Open the socket, passing the JWT in the connect handshake."""
        self.sio.connect(
            SERVER_URL,
            auth={"token": self.token} if self.token else None,
            wait_timeout=10,
        )

    def join(self, session_id: int, timeout: float = 5.0) -> tuple[str, dict]:
        """Emit join_session; returns the ("joined"|"join_error", payload) reply."""
        self.sio.emit("join_session", {"session_id": session_id})
        return self.wait_for(("joined", "join_error"), timeout=timeout)

    def wait_for(self, names, timeout: float = 3.0) -> tuple[str, dict]:
        """Block until one of `names` arrives, or fail the step."""
        if isinstance(names, str):
            names = (names,)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"{self.label}: no {' / '.join(names)} event within {timeout}s"
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
        except Exception:
            pass


def assert_join_error(label: str, reply: tuple[str, dict]) -> dict:
    event, payload = reply
    assert event == "join_error", f"{label} was allowed to join: {payload}"
    for key in ("check", "error", "message"):
        assert payload.get(key), f"join_error is missing '{key}': {payload}"
    return payload


def assert_checkin_payload(payload: dict, student: dict, count: int) -> None:
    assert payload.get("student_id") == student["id"], f"wrong student in event: {payload}"
    assert payload.get("full_name") == student["full_name"], f"wrong full_name: {payload}"
    assert payload.get("roll_number") == student["roll_number"], f"wrong roll_number: {payload}"
    assert payload.get("count") == count, f"expected count {count}: {payload}"
    stamp = payload.get("checked_in_at")
    assert isinstance(stamp, str) and stamp, f"missing checked_in_at: {payload}"
    datetime.fromisoformat(stamp)  # raises if it is not a parseable ISO timestamp


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
    AUTH["conductor"], STATE["conductor_id"] = register_and_login(
        "/auth/register/faculty",
        {
            "employee_id": f"EMP-SOCK-{RUN_ID}",
            "full_name": "Dr. Socket Conductor",
            "department": "Computer Science",
            "email": CONDUCTOR_EMAIL,
            "password": PASSWORD,
        },
        CONDUCTOR_EMAIL,
    )
    AUTH["other_faculty"], STATE["other_faculty_id"] = register_and_login(
        "/auth/register/faculty",
        {
            "employee_id": f"EMP-SOCK2-{RUN_ID}",
            "full_name": "Dr. Socket Outsider",
            "department": "Computer Science",
            "email": OTHER_FACULTY_EMAIL,
            "password": PASSWORD,
        },
        OTHER_FACULTY_EMAIL,
    )

    for student in STUDENTS:
        student["token"], student["id"] = register_and_login(
            "/auth/register/student",
            {
                "full_name": student["full_name"],
                "roll_number": student["roll_number"],
                "batch": "2022-2026",
                "department": "Computer Science",
                "email": student["email"],
                "password": PASSWORD,
            },
            student["email"],
        )

    with APP.app_context():
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=STATE["conductor_id"],
            room="SOCKET-TEST",
            course_code=COURSE_CODE,
            start_time=start,
            end_time=start + timedelta(hours=1),
            day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        STATE["class_id"] = scheduled_class.id
    print(
        f"   conductor={STATE['conductor_id']} other_faculty={STATE['other_faculty_id']} "
        f"students={[s['id'] for s in STUDENTS]} class_id={STATE['class_id']}"
    )


def step_students_ready():
    for student in STUDENTS:
        status, payload = api(
            "POST", "/student/enroll-face", token=student["token"],
            json={"name": student["full_name"], "face_image": enroll_image(student)},
        )
        assert status == 201, f"{student['label']} enroll-face returned {status}: {payload}"
        student["row_owned"] = bool(payload.get("created"))

        status, payload = api(
            "POST", "/student/register-device", token=student["token"],
            json={"device_id": student["device_id"]},
        )
        assert status == 200, f"{student['label']} register-device returned {status}: {payload}"
        assert payload["student"]["device_id"] == student["device_id"], f"bad binding: {payload}"
    print("   both students enrolled and device-bound")


def step_start_session():
    status, payload = api(
        "POST", "/session/start", token=AUTH["conductor"],
        json={"class_id": STATE["class_id"]},
    )
    assert status == 201, f"session/start returned {status}: {payload}"
    STATE["session_id"] = payload["session"]["id"]
    print(f"   session={STATE['session_id']} rotation={payload.get('rotation_seconds')}s")


def step_connect_without_token():
    anonymous = LiveClient("no-token")
    try:
        anonymous.connect()
    except SocketConnectionError as exc:
        print(f"   connection refused: {exc}")
        return
    raise AssertionError("a socket with no token was allowed to connect")


def step_conductor_joins():
    client = LiveClient("conductor", AUTH["conductor"])
    client.connect()
    event, payload = client.join(STATE["session_id"])
    assert event == "joined", f"conductor could not join: {payload}"
    assert payload.get("session_id") == STATE["session_id"], f"wrong session: {payload}"
    assert payload.get("room") == f"session:{STATE['session_id']}", f"wrong room: {payload}"
    assert payload.get("count") == 0, f"a fresh session must report 0 check-ins: {payload}"
    STATE["conductor_client"] = client
    print(f"   joined {payload['room']} as {payload.get('role')} (count={payload['count']})")


def step_student_cannot_join():
    client = LiveClient("student-socket", STUDENTS[0]["token"])
    client.connect()
    payload = assert_join_error("a student", client.join(STATE["session_id"]))
    client.close()
    print(f"   refused at check '{payload['check']}': {payload['message']}")


def step_other_faculty_cannot_join():
    client = LiveClient("other-faculty", AUTH["other_faculty"])
    client.connect()
    payload = assert_join_error("another faculty member", client.join(STATE["session_id"]))
    client.close()
    print(f"   refused at check '{payload['check']}': {payload['message']}")


def step_first_scan_is_broadcast():
    status, payload = scan_live(STUDENTS[0], use_code=False)
    assert status == 200, f"QR-nonce scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"

    event, live = STATE["conductor_client"].wait_for("student_checked_in", timeout=3.0)
    assert_checkin_payload(live, STUDENTS[0], 1)
    print(f"   {event}: {live['full_name']} ({live['roll_number']}) count={live['count']}")


def step_second_scan_increments_count():
    status, payload = scan_live(STUDENTS[1], use_code=True)
    assert status == 200, f"numeric-code scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"

    event, live = STATE["conductor_client"].wait_for("student_checked_in", timeout=3.0)
    assert_checkin_payload(live, STUDENTS[1], 2)
    print(f"   {event}: {live['full_name']} ({live['roll_number']}) count={live['count']}")


def step_attendees_endpoint():
    status, payload = api(
        "GET", f"/session/{STATE['session_id']}/attendees", token=AUTH["conductor"]
    )
    assert status == 200, f"GET /attendees returned {status}: {payload}"
    assert payload.get("count") == 2, f"expected 2 attendees: {payload}"
    attendees = payload.get("attendees")
    assert isinstance(attendees, list) and len(attendees) == 2, f"bad attendees list: {payload}"
    for index, student in enumerate(STUDENTS):  # oldest first = scan order
        assert_checkin_payload(attendees[index], student, index + 1)

    status, denied = api(
        "GET", f"/session/{STATE['session_id']}/attendees", token=AUTH["other_faculty"]
    )
    assert status == 403, f"another faculty must not read the roster, got {status}: {denied}"
    print(f"   roster: {[a['roll_number'] for a in attendees]} (other faculty 403)")


def step_end_session():
    nonce_before_end = get_qr()["current_nonce"]
    status, payload = api(
        "POST", f"/session/{STATE['session_id']}/end", token=AUTH["conductor"]
    )
    assert status == 200, f"POST /end returned {status}: {payload}"
    assert payload.get("count") == 2, f"expected count 2 in the /end response: {payload}"
    assert payload["session"]["is_active"] is False, f"session is still active: {payload}"

    event, live = STATE["conductor_client"].wait_for("session_ended", timeout=3.0)
    assert live.get("count") == 2, f"expected count 2 in {event}: {live}"
    STATE["last_nonce"] = nonce_before_end
    print(f"   {event}: count={live['count']} ended_at={payload['ended_at']}")


def step_scan_after_end_rejected():
    status, payload = scan(STUDENTS[0], use_code=False, nonce=STATE["last_nonce"])
    assert status == 409, f"a scan after /end must be 409, got {status}: {payload}"
    assert payload.get("check") == "session", f"unexpected check: {payload}"
    assert payload.get("error") == "session_ended", f"unexpected error: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_student_cannot_end():
    status, payload = api(
        "POST", f"/session/{STATE['session_id']}/end", token=STUDENTS[0]["token"]
    )
    assert status == 403, f"a student ending a session must be 403, got {status}: {payload}"
    print(f"   rejected 403: {payload.get('message') or payload.get('error')}")


# --------------------------------------------------------------------------
# Cleanup - only this run's rows
# --------------------------------------------------------------------------
def cleanup() -> str:
    for client in CLIENTS:
        client.close()

    emails = [CONDUCTOR_EMAIL, OTHER_FACULTY_EMAIL] + [s["email"] for s in STUDENTS]
    removed = 0
    with APP.app_context():
        user_ids = [u.id for u in User.query.filter(User.email.in_(emails)).all()]
        class_ids = [c.id for c in ScheduledClass.query.filter_by(course_code=COURSE_CODE).all()]
        session_ids = [
            s.id
            for s in AttendanceSession.query.filter(
                AttendanceSession.class_id.in_([str(class_id) for class_id in class_ids]),
            ).all()
        ]
        removed += AttendanceCheckIn.query.filter(
            AttendanceCheckIn.session_id.in_(session_ids),
        ).delete(synchronize_session=False)
        removed += AttendanceSession.query.filter(
            AttendanceSession.id.in_(session_ids),
        ).delete(synchronize_session=False)
        removed += ScheduledClass.query.filter(ScheduledClass.id.in_(class_ids)).delete(
            synchronize_session=False,
        )
        owned_student_ids = [s["id"] for s in STUDENTS if s["row_owned"] and s["id"] is not None]
        if owned_student_ids:
            removed += Student.query.filter(Student.id.in_(owned_student_ids)).delete(
                synchronize_session=False,
            )
        for model in (FacultyProfile, StudentProfile):
            removed += model.query.filter(model.user_id.in_(user_ids)).delete(
                synchronize_session=False,
            )
        removed += User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.session.commit()
        assert User.query.filter(User.email.in_(emails)).count() == 0
        assert ScheduledClass.query.filter_by(course_code=COURSE_CODE).count() == 0
    return f"removed {removed} local rows (on-chain history is permanent)"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Auth setup: conductor, other faculty, two students, class", step_auth_setup),
    ("Students enrolled and device-bound", step_students_ready),
    ("Start attendance session (conductor JWT)", step_start_session),
    ("Socket connect without a token -> refused", step_connect_without_token),
    ("Conductor joins session room", step_conductor_joins),
    ("Student token cannot join", step_student_cannot_join),
    ("Other faculty cannot join", step_other_faculty_cannot_join),
    ("Scan (QR nonce) -> student_checked_in count 1", step_first_scan_is_broadcast),
    ("Scan (numeric code) -> student_checked_in count 2", step_second_scan_increments_count),
    ("GET /session/<id>/attendees returns both, oldest first", step_attendees_endpoint),
    ("POST /session/<id>/end -> session_ended {count: 2}", step_end_session),
    ("Scan after /end -> session_ended", step_scan_after_end_rejected),
    ("Student cannot POST /session/<id>/end", step_student_cannot_end),
]


def main() -> int:
    print("=" * 60)
    print("   LIVE ATTENDANCE SOCKET TESTS (test_socket.py)")
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
        except SocketConnectionError as exc:
            print(f"   \u274c FAIL: socket connection failed ({exc}) - is run.py using socketio.run?")
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
        print(f"   ALL LIVE ATTENDANCE SOCKET TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0 if failed is None and cleanup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
