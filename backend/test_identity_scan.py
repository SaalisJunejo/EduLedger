"""End-to-end test of the Module 1 identity verification + scan flow.

Run it against a live stack (from backend/):

    .venv\\Scripts\\python.exe test_identity_scan.py

Prerequisites:
    - Hardhat node running        (contracts/: npm run node)
    - AttendanceLedger deployed   (contracts/: npm run deploy:attendance)
    - Flask backend running       (backend/: python run.py)

Steps (each prints a clear PASS/FAIL line; the first failure stops the run
with the failing response, and the script exits non-zero - no uncaught
exceptions):

     0. preflight          - GET /health (backend reachable, DB + RPC configured)
     1. enroll-face        - demo enrollment: upserts student 1 + face embedding
     2. register-device    - binds VALID_DEVICE (falls back to admin rebind)
     3. verify-identity    - face match -> 30-second verification token (JWT)
     4. session/start + qr - live session with a rotating nonce
     5. scan (valid)       - checks (a)-(d) pass -> lockAttendance() on-chain
     6. scan (re-scan)     - rejected 409 attendance_already_locked  [check d]
     7. scan (fake nonce)  - rejected 400 nonce_mismatch             [check b]
     8. scan (aged nonce)  - a once-valid nonce replayed after its
                            rotation window: rejected 400 (nonce_mismatch
                            or nonce_expired)                        [check b]
     9. scan (bad device)  - rejected 403 device_mismatch            [check c]
    10. verify-identity    - stranger's face rejected 401 identity_verification_failed

API contract notes (aligned with the backend implementation in app/api/):
    - Student.id is an INTEGER primary key (numeric id 1 - not "STU1001").
    - /attendance/scan takes "nonce" (not "scanned_nonce") and does NOT take
      student_id: the student comes from the verification token's "sub" claim.
    - A successful scan returns "transaction_hash" (not "tx_hash").
    - There is no auth-header middleware yet (no X-Device-ID header): the
      device identity travels in the JSON body. If an auth layer lands later,
      the status assertions below will flag it immediately.
"""

import base64
import io
import sys
import time

import requests
from PIL import Image

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Standard test inputs. Student.id is an Integer PK: numeric 1, not "STU1001".
STUDENT_ID = 1
STUDENT_NAME = "Ayesha Khan"
VALID_DEVICE = "device_keychain_uuid_12345"
INVALID_DEVICE = "hacker_phone_uuid_99999"
TEST_CLASS_ID = "TEST-IDENTITY-SCAN"

# Shared state between steps (filled in as the run progresses).
STATE = {"session_id": None, "token": None}


# --------------------------------------------------------------------------
# Synthetic "face" images (valid, decodable PNGs - the backend rejects the
# truncated placeholder base64 strings, so real bytes are required).
# --------------------------------------------------------------------------
def _png_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _gradient_image(shift: int = 0) -> Image.Image:
    """A smooth horizontal gradient - the enrolled student's 'face'."""
    image = Image.new("L", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            pixels[x, y] = max(0, min(255, (x + shift) * 255 // 95))
    return image


def _noise_image() -> Image.Image:
    """Seeded random noise - a stranger's 'face' (dissimilar to the gradient)."""
    import random

    rng = random.Random(1234)
    image = Image.new("L", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            pixels[x, y] = rng.randrange(256)
    return image


ENROLL_IMAGE = _png_data_uri(_gradient_image(shift=0))
CAPTURE_IMAGE = _png_data_uri(_gradient_image(shift=2))  # same "face", tiny capture difference
STRANGER_IMAGE = _png_data_uri(_noise_image())


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


def fresh_nonce() -> str:
    """The session's current live nonce (the QR endpoint lazily rotates expired
    nonces, so this always returns one that /attendance/scan will accept)."""
    status, payload = api("GET", f"/session/{STATE['session_id']}/qr")
    assert status == 200, f"GET /session/{STATE['session_id']}/qr returned {status}: {payload}"
    assert payload["qr_image"].startswith("data:image/png;base64,"), "QR image is not a PNG data URI"
    return payload["current_nonce"]


def verify_identity(face_image: str) -> tuple[int, dict]:
    """POST /attendance/verify-identity with the given face image."""
    return api(
        "POST",
        "/attendance/verify-identity",
        json={"student_id": STUDENT_ID, "face_image": face_image},
    )


def verification_token() -> str:
    """A freshly minted (30 s) verification token for the enrolled student."""
    status, payload = verify_identity(CAPTURE_IMAGE)
    assert status == 200, f"verify-identity returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, f"no verification_token in response: {payload}"
    return token


def scan(token: str, device_id: str, nonce: str) -> tuple[int, dict]:
    """POST /attendance/scan - field names per the API contract:
    'nonce' (not 'scanned_nonce'), no student_id (token-derived)."""
    return api(
        "POST",
        "/attendance/scan",
        json={
            "verification_token": token,
            "device_id": device_id,
            "session_id": STATE["session_id"],
            "nonce": nonce,
        },
    )


def scan_with_live_nonce(token: str, device_id: str) -> tuple[int, dict]:
    """Scan with a nonce fetched immediately before, retrying once if the 5 s
    rotation window flipped between fetch and scan (rare race; the retry keeps
    steps that test OTHER checks from failing on nonce timing)."""
    status, payload = scan(token, device_id, fresh_nonce())
    if payload.get("check") == "session_nonce":
        print("   note: nonce rotated mid-flight - retrying with a fresh nonce")
        status, payload = scan(token, device_id, fresh_nonce())
    return status, payload


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Backend reachable and configured (DB + blockchain RPC)."""
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    print(
        f"   service={payload['service']} env={payload['environment']} "
        f"db={deps['database_configured']} rpc={deps['blockchain_rpc_configured']} "
        f"attendance_contract={deps['contracts_configured']['attendance_engine']}"
    )


def step_enroll_face():
    """Demo enrollment: create (or update) student 1 and store the face embedding.
    Required before verify-identity - without it the backend answers 409
    face_not_enrolled."""
    status, payload = api(
        "POST",
        "/student/enroll-face",
        json={"student_id": STUDENT_ID, "name": STUDENT_NAME, "face_image": ENROLL_IMAGE},
    )
    assert status == 201, f"enroll-face returned {status}: {payload}"
    student = payload["student"]
    assert student["id"] == STUDENT_ID, f"unexpected student id: {student}"
    assert student["has_face_embedding"] is True, "face embedding was not stored"
    print(
        f"   student={student['id']} name={student['name']!r} "
        f"provider={payload['embedding']['provider']} dims={payload['embedding']['dims']} "
        f"created={payload['created']}"
    )


def step_register_device():
    """Bind the student's device. First binding only: if a previous run left a
    DIFFERENT device bound, the endpoint answers 409 device_already_bound and
    the admin rebind stub is the designed recovery path."""
    status, payload = api(
        "POST",
        "/student/register-device",
        json={"student_id": STUDENT_ID, "device_id": VALID_DEVICE},
    )
    if status == 409 and payload.get("error") == "device_already_bound":
        print(f"   note: another device is bound - rebinding via the admin stub")
        status, payload = api(
            "POST",
            "/admin/rebind-device",
            json={"student_id": STUDENT_ID, "device_id": VALID_DEVICE},
        )
    assert status == 200, f"register-device returned {status}: {payload}"
    assert payload["student"]["device_id"] == VALID_DEVICE, f"unexpected binding: {payload}"
    print(f"   device bound: {payload['student']['device_id']}")


def step_verify_identity():
    """Face match against the stored embedding -> 30 s verification token."""
    status, payload = verify_identity(CAPTURE_IMAGE)
    assert status == 200, f"verify-identity returned {status}: {payload}"
    token = payload.get("verification_token")
    assert token, "no verification_token in response"
    STATE["token"] = token
    assert payload["similarity"] >= payload["threshold"], (
        f"similarity {payload['similarity']} below threshold {payload['threshold']}"
    )
    print(
        f"   method={payload['method']} similarity={payload['similarity']} "
        f"threshold={payload['threshold']} expires_in={payload['expires_in']}s"
    )


def step_start_session():
    """Open a live attendance session for the test class."""
    status, payload = api("POST", "/session/start", json={"class_id": TEST_CLASS_ID})
    assert status == 201, f"session/start returned {status}: {payload}"
    session = payload["session"]
    assert session["is_active"] is True, f"new session is not active: {session}"
    STATE["session_id"] = session["id"]
    print(
        f"   session={session['id']} class={session['class_id']!r} "
        f"rotation={payload['rotation_seconds']}s qr_poll_url={payload['qr_poll_url']}"
    )


def step_valid_scan():
    """The happy path: all four checks pass and attendance is locked on-chain."""
    status, payload = scan_with_live_nonce(STATE["token"], VALID_DEVICE)
    assert status == 200, f"valid scan was rejected: {status} {payload}"
    assert payload.get("status") == "locked", f"unexpected scan status: {payload}"
    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"
    print(
        f"   locked on-chain: tx={tx_hash} block={payload['block_number']} "
        f"locked_at={payload['locked_at']} signer={payload['signer']}"
    )


def step_rescan_blocked():
    """Re-scan of the same student+session: check (d) rejects it on-chain.
    Uses a fresh token + fresh nonce so the scan reliably REACHES check (d)."""
    status, payload = scan_with_live_nonce(verification_token(), VALID_DEVICE)
    assert status == 409, f"re-scan must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "attendance_already_locked", f"unexpected error: {payload}"
    assert payload.get("check") == "onchain_lock", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_bad_nonce_blocked():
    """A photographed/replayed QR: check (b) rejects the stale nonce."""
    status, payload = scan(verification_token(), VALID_DEVICE, "EXPIRED_OR_FAKE_NONCE_12345")
    assert status == 400, f"bad nonce must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "nonce_mismatch", f"unexpected error: {payload}"
    assert payload.get("check") == "session_nonce", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_aged_nonce_blocked():
    """A nonce that WAS valid when captured, scanned after its rotation window:
    the photographed-QR replay. Rejection is check (b) with nonce_mismatch
    (already rotated) or nonce_expired (expired but the scheduler has not
    rotated it yet) - both are correct 'old nonce' rejections."""
    token = verification_token()
    aged = fresh_nonce()
    print(f"   captured nonce {aged[:22]}... - waiting out the rotation window")
    time.sleep(6)  # NONCE_ROTATION_SECONDS is 5: the captured nonce is now stale
    status, payload = scan(token, VALID_DEVICE, aged)
    assert status == 400, f"aged nonce must be rejected with 400, got {status}: {payload}"
    assert payload.get("check") == "session_nonce", f"unexpected check: {payload}"
    assert payload.get("error") in ("nonce_mismatch", "nonce_expired"), (
        f"unexpected error code: {payload}"
    )
    print(
        f"   rejected at check '{payload['check']}' ({payload['error']}): {payload['message']}"
    )


def step_bad_device_blocked():
    """A different phone scanning the same QR: check (c) rejects the device.
    Uses a fresh nonce so the scan passes check (b) and reaches check (c)."""
    status, payload = scan_with_live_nonce(verification_token(), INVALID_DEVICE)
    assert status == 403, f"bad device must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "device_mismatch", f"unexpected error: {payload}"
    assert payload.get("check") == "device_match", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_stranger_face_blocked():
    """Someone else's face at verify-identity: rejected below the threshold."""
    status, payload = verify_identity(STRANGER_IMAGE)
    assert status == 401, f"stranger face must be rejected with 401, got {status}: {payload}"
    assert payload.get("error") == "identity_verification_failed", f"unexpected error: {payload}"
    assert payload.get("check") == "identity", f"unexpected check: {payload}"
    assert payload["similarity"] < payload["threshold"], (
        f"stranger similarity {payload['similarity']} not below threshold {payload['threshold']}"
    )
    print(
        f"   rejected at check '{payload['check']}': similarity={payload['similarity']} "
        f"< threshold={payload['threshold']}"
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Enroll student face (demo enrollment)", step_enroll_face),
    ("Register device (bind deviceId to student)", step_register_device),
    ("Verify identity (face match -> 30s token)", step_verify_identity),
    ("Start attendance session + fetch live nonce", step_start_session),
    ("Valid scan -> lock attendance on-chain", step_valid_scan),
    ("Re-scan -> blocked, already locked on-chain", step_rescan_blocked),
    ("Scan with fake nonce -> blocked", step_bad_nonce_blocked),
    ("Scan with aged (rotated) nonce -> blocked", step_aged_nonce_blocked),
    ("Scan from unregistered device -> blocked", step_bad_device_blocked),
    ("Stranger's face at verify-identity -> blocked", step_stranger_face_blocked),
]


def main() -> int:
    print("=" * 60)
    print("   IDENTITY & SCAN VALIDATION TESTS (test_identity_scan.py)")
    print("=" * 60)

    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            print("      start it first:  .venv\\Scripts\\python.exe run.py   (from backend/)")
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
    print(f"   ALL IDENTITY & SCAN TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
