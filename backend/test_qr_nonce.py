import time
import requests

BASE_URL = "http://localhost:5000/api/v1"

def test_qr_rotation():
    print("--- 1. Starting Session ---")
    res = requests.post(f"{BASE_URL}/session/start", json={"class_id": "1", "duration_minutes": 10})
    assert res.status_code in (200, 201), f"Failed to start session: {res.text}"
    
    session_data = res.json()
    print(f"Backend Response: {session_data}")
    
    # Extract session ID from top-level or nested 'session' object
    session_info = session_data.get("session", {})
    session_id = (
        session_data.get("sessionId") 
        or session_data.get("id") 
        or session_info.get("id")
    )
    
    assert session_id is not None, "No session ID returned in response!"
    print(f"Active session ID created: {session_id}")

    def get_qr():
        response = requests.get(f"{BASE_URL}/session/{session_id}/qr")
        assert response.status_code == 200, f"Failed GET /qr: {response.text}"
        return response.json()

    print("\n--- 2. Fetching Initial Nonce ---")
    qr_initial = get_qr()
    
    print("Waiting 1 second to align with window start...")
    time.sleep(1)

    print("\n--- 3. Polling QR Endpoint (Poll 1) ---")
    qr1 = get_qr()
    nonce1 = qr1.get("current_nonce") or qr1.get("nonce")
    print("Poll 1 Nonce:", nonce1)

    print("\n--- 4. Polling after 1.5 seconds (Poll 2 - Same Window) ---")
    time.sleep(1.5)
    qr2 = get_qr()
    nonce2 = qr2.get("current_nonce") or qr2.get("nonce")
    print("Poll 2 Nonce:", nonce2)

    assert nonce1 == nonce2, (
        f"❌ FAILED: Nonce changed within same 5s window!\n"
        f"Poll 1: {nonce1}\n"
        f"Poll 2: {nonce2}"
    )
    print("✅ PASSED: Nonce remained identical within 5-second window.")

    print("\n--- 5. Waiting 5.5 seconds (Poll 3 - Next Window) ---")
    time.sleep(5.5)
    qr3 = get_qr()
    nonce3 = qr3.get("current_nonce") or qr3.get("nonce")
    print("Poll 3 Nonce:", nonce3)

    assert nonce1 != nonce3, "❌ FAILED: Nonce did NOT rotate after 5+ seconds!"
    print("✅ PASSED: Nonce successfully rotated across windows.")

if __name__ == "__main__":
    test_qr_rotation()