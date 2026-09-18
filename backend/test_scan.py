import requests

BASE = "http://127.0.0.1:5000/api/v1"

# Registered device ID for Student 1
BOUND_DEVICE_ID = "device_keychain_uuid_12345"

print("--- Step 1: Starting Session ---")
s_req = requests.post(f"{BASE}/session/start", json={"class_id": "BSCS-401"})
s_res = s_req.json()

session_data = s_res.get("session", s_res)
session_id = session_data.get("id", 1)
nonce = session_data.get("current_nonce")

print(f"Active Session ID: {session_id} | Active Nonce: {nonce}")

print("\n--- Step 2: Registering Device ---")
# Device is already bound; verifying current binding status
dev_res = requests.post(f"{BASE}/student/register-device", json={
    "student_id": 1,
    "device_id": BOUND_DEVICE_ID
})
print("Device Register Status:", dev_res.status_code)

print("\n--- Step 3: Verifying Identity (Fingerprint WebAuthn Stub) ---")
# Uses webauthn_assertion stub to pass biometric identity check locally
v_req = requests.post(f"{BASE}/attendance/verify-identity", json={
    "student_id": 1,
    "device_id": BOUND_DEVICE_ID,
    "webauthn_assertion": {
        "credential_id": "stub_cred_id",
        "authenticator_data": "stub_auth_data",
        "client_data_json": "stub_client_data",
        "signature": "stub_sig"
    }
})
print("Verify Identity Status:", v_req.status_code)
print("Verify Identity Response Body:", v_req.text)

v_data = v_req.json()
token = v_data.get("verification_token") or v_data.get("token")
print(f"Extracted Verification Token: {token}")

print("\n--- Step 4: Attendance Scan ---")
scan_res = requests.post(f"{BASE}/attendance/scan", json={
    "verification_token": token,
    "nonce": nonce,
    "device_id": BOUND_DEVICE_ID,
    "session_id": session_id
})
print("Scan Response Status:", scan_res.status_code)
print("Scan Response Body:", scan_res.text)