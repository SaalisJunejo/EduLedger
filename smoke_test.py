import requests
import sys

BASE_URL = "http://127.0.0.1:5000"

def run_smoke_test():
    print("--- Running EduLedger Smoke Test ---")
    
    # 1. Test Backend Connection & Session Start
    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/session/start", 
            json={"class_id": "CS102"}, 
            timeout=5
        )
        if response.status_code in [200, 201]:
            print("[PASS] Backend API - Session Start")
            session_id = response.json().get("id", 1)
        else:
            print(f"[FAIL] Backend API - Session Start (Status {response.status_code})")
            session_id = 1
    except Exception as e:
        print(f"[FAIL] Backend API Connection: {e}")
        sys.exit(1)

    import time
    time.sleep(1)
    # 2. Test Nonce & QR Generation Endpoint
    try:
        response = requests.get(f"{BASE_URL}/api/v1/session/{session_id}/qr", timeout=5)
        if response.status_code == 200:
            print("[PASS] QR & Nonce Generation Endpoint")
        else:
            print(f"[FAIL] QR Endpoint returned status {response.status_code}")
    except Exception as e:
        print(f"[FAIL] QR Endpoint Request failed: {e}")

    print("--- Smoke Test Completed ---")

if __name__ == "__main__":
    run_smoke_test()