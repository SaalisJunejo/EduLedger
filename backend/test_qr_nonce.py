import os
import sys
import time
from datetime import timedelta
from uuid import uuid4

import requests

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app
from app.extensions import db
from app.models.workload import ScheduledClass
from app.util import utcnow

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:5000/api/v1"


def setup_auth() -> tuple[str, int]:
    run_id = uuid4().hex
    faculty_email = f"faculty.qr.{run_id}@gcu.edu.pk"
    faculty_pass = "FacultyPass123!"
    registration = requests.post(
        f"{BASE_URL}/auth/register/faculty",
        json={
            "employee_id": f"EMP-QR-{run_id}",
            "full_name": "Dr. QR Faculty",
            "department": "Computer Science",
            "email": faculty_email,
            "password": faculty_pass,
        },
    )
    assert registration.status_code == 201, f"Faculty registration failed: {registration.text}"
    login_response = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": faculty_email, "password": faculty_pass},
    )
    res = login_response.json()
    assert login_response.status_code == 200, f"Faculty login failed: {res}"
    token = res.get("token")
    assert token, f"Faculty login failed: {res}"
    assert res["user"]["id"] == registration.json()["user"]["id"], "Faculty user ID changed at login"
    return token, res["user"]["id"]


def test_qr_rotation():
    faculty_token, faculty_id = setup_auth()
    headers = {"Authorization": f"Bearer {faculty_token}"}

    with create_app().app_context():
        start = utcnow() - timedelta(seconds=5)
        scheduled_class = ScheduledClass(
            professor_id=faculty_id,
            room="QR-NONCE-TEST",
            course_code="TEST-QR-NONCE",
            start_time=start,
            end_time=start + timedelta(hours=1),
            day_of_week=None,
        )
        db.session.add(scheduled_class)
        db.session.commit()
        class_id = scheduled_class.id

    print("--- 1. Starting Session ---")
    res = requests.post(
        f"{BASE_URL}/session/start",
        headers=headers,
        json={"class_id": class_id, "duration_minutes": 10},
    )
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
        response = requests.get(f"{BASE_URL}/session/{session_id}/qr", headers=headers)
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