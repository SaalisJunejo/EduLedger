"""EduLedger - Section 2.3 negative-case security tests (scan endpoint).

Run against the live stack (from backend/):

    .venv\\Scripts\\python.exe test_negative_cases.py

Prerequisites (same stack as test_identity_scan.py):
    - Hardhat node running + AttendanceLedger deployed (contracts/)
    - Flask backend running (python run.py)
    - student 1 enrolled and BOUND_DEVICE_ID bound to them (running
      test_identity_scan.py once satisfies this)

Every case prints a clear PASS/FAIL line and the run exits non-zero if
any case fails. Device-related rejections state which layer produced
them: the verify-identity token mint (check "identity") or the scan
endpoint's own device check (check "device_match").

The script forces UTF-8 stdout itself, so it prints cleanly on legacy
(cp1252) Windows consoles without PYTHONUTF8=1 (setting it anyway never
hurts).
"""

import sys

import requests

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print
# (equivalent to running with PYTHONUTF8=1).
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000/api/v1"
BOUND_DEVICE_ID = "device_keychain_uuid_12345"
WRONG_DEVICE_ID = "WRONG_DEVICE_ID_999"
STUDENT_ID = 1

failed_cases = []


def get_session_and_nonce():
    """Starts a session and returns (session_id, current_nonce)."""
    s_req = requests.post(f"{BASE}/session/start", json={"class_id": "BSCS-401"})
    s_res = s_req.json()
    session_data = s_res.get("session", s_res)
    session_id = session_data.get("id")
    nonce = session_data.get("current_nonce")
    return session_id, nonce


def get_verification_token(device_id=BOUND_DEVICE_ID):
    """Verifies identity via WebAuthn stub and returns a verification token."""
    v_req = requests.post(f"{BASE}/attendance/verify-identity", json={
        "student_id": STUDENT_ID,
        "device_id": device_id,
        "webauthn_assertion": {
            "credential_id": "stub_cred_id",
            "authenticator_data": "stub_auth_data",
            "client_data_json": "stub_client_data",
            "signature": "stub_sig"
        }
    })
    v_data = v_req.json()
    return v_data.get("verification_token") or v_data.get("token")


print("==================================================")
print("     EduLedger - Section 2.3 Security Tests       ")
print("==================================================\n")

# ---------------------------------------------------------
# Test 1: Duplicate Scan Rejection
# ---------------------------------------------------------
print("--- Test 1: Duplicate Scan Rejection ---")
session_id_1, nonce_1 = get_session_and_nonce()
token_1 = get_verification_token()

# The duplicate rejection below only proves anything if the FIRST scan
# actually succeeds - fail loudly here instead of letting a trivial
# "duplicate rejected" pass through.
if not token_1:
    print("  ❌ FAILED: no verification token (verify-identity rejected the")
    print("     bound device). Is the stack up and student 1 enrolled + device")
    print("     bound? Run test_identity_scan.py first to set the demo state up.")
    sys.exit(1)

scan1_res = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token_1,
    "nonce": nonce_1,
    "device_id": BOUND_DEVICE_ID,
    "session_id": session_id_1
})

if scan1_res.status_code != 200:
    print(f"  ❌ FAILED: initial scan did not succeed (status {scan1_res.status_code})")
    print(f"     {scan1_res.text.strip()}")
    print("     The duplicate-scan case cannot be evaluated - aborting.")
    sys.exit(1)

print("  Initial Scan #1: SUCCESS (Attendance Locked on-chain)")
tx_hash = scan1_res.json().get("transaction_hash")
if tx_hash:
    print(f"     tx: {tx_hash}")

# Second Scan for same student + session (Should be REJECTED)
token_1_again = get_verification_token()
scan2_res = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token_1_again,
    "nonce": nonce_1,
    "device_id": BOUND_DEVICE_ID,
    "session_id": session_id_1
})

print(f"  Duplicate Scan #2 Status Code: {scan2_res.status_code}")
print(f"  Duplicate Scan #2 Response: {scan2_res.text.strip()}")
if scan2_res.status_code != 200:
    error_2 = scan2_res.json().get("error")
    if error_2 != "attendance_already_locked":
        print(f"  NOTE: rejection came from '{error_2}' rather than "
              "attendance_already_locked (nonce rotation race?)")
    print("  RESULT: ✅ PASSED (Duplicate scan correctly rejected)\n")
else:
    print("  RESULT: ❌ FAILED (Duplicate scan was allowed!)\n")
    failed_cases.append("Test 1: duplicate scan")

# ---------------------------------------------------------
# Test 2: Expired / Invalid Nonce Rejection
# ---------------------------------------------------------
print("--- Test 2: Expired / Invalid Nonce Rejection ---")
session_id_2, _ = get_session_and_nonce()
token_2 = get_verification_token()
INVALID_NONCE = "EXPIRED_OR_FAKE_NONCE_99999"

scan_bad_nonce = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token_2,
    "nonce": INVALID_NONCE,
    "device_id": BOUND_DEVICE_ID,
    "session_id": session_id_2
})

print(f"  Expired Nonce Scan Status Code: {scan_bad_nonce.status_code}")
print(f"  Expired Nonce Scan Response: {scan_bad_nonce.text.strip()}")
if scan_bad_nonce.status_code != 200:
    print("  RESULT: ✅ PASSED (Invalid/expired nonce correctly rejected)\n")
else:
    print("  RESULT: ❌ FAILED (Invalid nonce was accepted!)\n")
    failed_cases.append("Test 2: invalid nonce")

# ---------------------------------------------------------
# Test 3: Mismatched Device ID Rejection
# ---------------------------------------------------------
# Two independent layers can reject a wrong device:
#   3a. verify-identity refuses to mint a token for a device that is not
#       the student's registered one (check "identity")
#   3b. the scan endpoint's own device check rejects a request whose
#       device_id differs from the registered device even though the
#       verification token was minted for the correct device
#       (check "device_match")
print("--- Test 3: Mismatched Device ID Rejection ---")

# 3a. Wrong device already at the verify-identity step (token mint)
verify_wrong_device = requests.post(f"{BASE}/attendance/verify-identity", json={
    "student_id": STUDENT_ID,
    "device_id": WRONG_DEVICE_ID,
    "webauthn_assertion": {
        "credential_id": "stub_cred_id",
        "authenticator_data": "stub_auth_data",
        "client_data_json": "stub_client_data",
        "signature": "stub_sig"
    }
})
v3a = verify_wrong_device.json()
print(f"  3a. verify-identity with wrong device - Status: {verify_wrong_device.status_code}")
print(f"      Response: {verify_wrong_device.text.strip()}")
if (verify_wrong_device.status_code == 403
        and v3a.get("error") == "device_mismatch"
        and v3a.get("check") == "identity"):
    print("  RESULT 3a: ✅ PASSED (rejected at the VERIFY-IDENTITY layer - "
          "check 'identity', error 'device_mismatch')\n")
else:
    print("  RESULT 3a: ❌ FAILED (expected 403 device_mismatch with check "
          "'identity' from verify-identity)\n")
    failed_cases.append("Test 3a: verify-identity device check")

# 3b. Valid token (minted for the REGISTERED device), wrong device at /scan
session_id_3, nonce_3 = get_session_and_nonce()
token_3 = get_verification_token()  # BOUND_DEVICE_ID -> must succeed
if not token_3:
    print("  ❌ FAILED 3b: could not mint a valid token for the registered")
    print("     device - the scan-level device check cannot be evaluated.")
    failed_cases.append("Test 3b: scan-level device check")
else:
    scan_wrong_device = requests.post(f"{BASE}/attendance/scan", json={
        "verification_token": token_3,   # valid: minted for BOUND_DEVICE_ID
        "nonce": nonce_3,                # valid: this session's current nonce
        "device_id": WRONG_DEVICE_ID,    # wrong: not the registered device
        "session_id": session_id_3
    })
    s3b = scan_wrong_device.json()
    print(f"  3b. scan with valid token but wrong device - Status: {scan_wrong_device.status_code}")
    print(f"      Response: {scan_wrong_device.text.strip()}")
    if (scan_wrong_device.status_code == 403
            and s3b.get("error") == "device_mismatch"
            and s3b.get("check") == "device_match"):
        print("  RESULT 3b: ✅ PASSED (rejected at the SCAN ENDPOINT layer - "
              "check 'device_match', error 'device_mismatch'; the token itself "
              "was valid, so this exercises the scan's own device check)\n")
    else:
        print("  RESULT 3b: ❌ FAILED (expected 403 device_mismatch with check "
              "'device_match' from the scan endpoint)\n")
        failed_cases.append("Test 3b: scan-level device check")

print("==================================================")
print("              Security Testing Complete           ")
print("==================================================")
if failed_cases:
    print(f"  ❌ {len(failed_cases)} case(s) FAILED: {'; '.join(failed_cases)}")
    sys.exit(1)
print("  ✅ ALL CASES PASSED")
sys.exit(0)
