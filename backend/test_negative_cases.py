import requests

BASE = "http://127.0.0.1:5000/api/v1"
BOUND_DEVICE_ID = "device_keychain_uuid_12345"
STUDENT_ID = 1

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

# First Scan (Should Succeed)
scan1_res = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token_1,
    "nonce": nonce_1,
    "device_id": BOUND_DEVICE_ID,
    "session_id": session_id_1
})

if scan1_res.status_code == 200:
    print("  Initial Scan #1: SUCCESS (Attendance Locked on-chain)")
else:
    print(f"  Initial Scan #1 Warning: Status {scan1_res.status_code} - {scan1_res.text}")

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
    print("  RESULT: ✅ PASSED (Duplicate scan correctly rejected)\n")
else:
    print("  RESULT: ❌ FAILED (Duplicate scan was allowed!)\n")

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

# ---------------------------------------------------------
# Test 3: Mismatched Device ID Rejection
# ---------------------------------------------------------
print("--- Test 3: Mismatched Device ID Rejection ---")
session_id_3, nonce_3 = get_session_and_nonce()
token_3 = get_verification_token(device_id="WRONG_DEVICE_ID_999")
WRONG_DEVICE_ID = "WRONG_DEVICE_ID_999"

scan_wrong_device = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token_3,
    "nonce": nonce_3,
    "device_id": WRONG_DEVICE_ID,
    "session_id": session_id_3
})

print(f"  Mismatched Device Scan Status Code: {scan_wrong_device.status_code}")
print(f"  Mismatched Device Scan Response: {scan_wrong_device.text.strip()}")
if scan_wrong_device.status_code != 200:
    print("  RESULT: ✅ PASSED (Mismatched device ID correctly rejected)\n")
else:
    print("  RESULT: ❌ FAILED (Mismatched device ID was accepted!)\n")

print("==================================================")
print("              Security Testing Complete           ")
print("==================================================")