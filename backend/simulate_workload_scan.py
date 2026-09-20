"""Development utility: simulate a student scanning a faculty workload QR.

Why this exists
---------------
The workload module's "any single scan concludes the class" endpoint is
POST /api/v1/faculty/session/scan — a different endpoint than the Module 1
/attendance/scan the student app calls (that one locks the *student's*
attendance and does not conclude the class). Until the student app learns
to detect a workload QR (Phase 4+), this script stands in for that student
scan during demos: it reads the live rotating nonce exactly like the QR
payload would and submits it, which anchors the class CONDUCTED on-chain
and flips the instructor dashboard's badge via the logs poll.

    cd backend
    py -3.12 simulate_workload_scan.py            # newest live workload session
    py -3.12 simulate_workload_scan.py 14         # a specific session id

The QR payload is {"session_id": <id>, "nonce": "<current nonce>"} — the
same shape GET /api/v1/session/<id>/qr encodes, read here straight from the
projector endpoint so the nonce is always current (retried if it rotates
between the read and the submit).

Development only.
"""

import json
import os
import sys
import urllib.request

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AttendanceSession  # noqa: E402

BASE_URL = "http://localhost:5000/api/v1"


def _post(path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _newest_workload_session_id() -> int:
    """Id of the newest still-active workload-* attendance session."""
    session = (
        AttendanceSession.query.filter(
            AttendanceSession.class_id.like("workload-%"),
            AttendanceSession.is_active.is_(True),
        )
        .order_by(AttendanceSession.id.desc())
        .first()
    )
    if session is None:
        raise SystemExit("no active workload session - start one from the UI first")
    return session.id


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        session_id = int(sys.argv[1]) if len(sys.argv) > 1 else _newest_workload_session_id()
        print(f"Simulating a student scan of workload session #{session_id}")

        # The nonce rotates every NONCE_ROTATION_SECONDS (5s); read and
        # submit immediately, retrying a couple of times if it rotates
        # in between (the real QR would simply be rescanned).
        for attempt in range(1, 4):
            qr = _get(f"/session/{session_id}/qr")
            status, body = _post(
                "/faculty/session/scan",
                {"session_id": session_id, "nonce": qr["current_nonce"]},
            )
            if status in (200, 201):
                print(f"[OK] HTTP {status} - {body.get('status')}")
                print(f"     class          : {body['session_log']['classId']}")
                print(f"     conducted by   : {body['session_log']['conductedBy']}")
                print(f"     transaction    : {body.get('transaction_hash')}")
                return 0
            if body.get("error") == "nonce_mismatch":
                print(f"  attempt {attempt}: nonce rotated - resubmitting")
                continue
            print(f"[FAILED] HTTP {status} - {body}")
            return 1

        print("[FAILED] nonce kept rotating - try again")
        return 1


if __name__ == "__main__":
    sys.exit(main())
