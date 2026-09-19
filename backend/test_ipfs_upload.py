"""End-to-end test of the IPFS evidence upload endpoint.

Run it against a live stack (from backend/):

    py -3.12 test_ipfs_upload.py

Prerequisites:
    - Flask backend running       (backend/: py -3.12 run.py)
    - IPFS daemon running         (kubo: `ipfs daemon`) OR WEB3_STORAGE_TOKEN
                                  set in backend/.env (free-tier fallback)
    - Internet access for the gateway reachability check (https://ipfs.io)

Steps (each prints a clear PASS/FAIL line; the first failure stops the run
with the failing response, and the script exits non-zero - no uncaught
exceptions):

     0. preflight        - GET /health (backend reachable, IPFS configured)
     1. upload evidence  - POST /ipfs/upload with a small PNG -> valid CID
     2. gateway URL      - the returned https://ipfs.io/ipfs/<cid> answers
     3. missing file     - rejected 400 missing_fields           [check request]
     4. wrong file type  - .txt rejected 400 invalid_file_type   [check request]
     5. oversized file   - 10 MB + 1 rejected 400 file_too_large [check request]

CID notes: the local Kubo daemon's `add` defaults to CIDv0 (Qm..., base58);
Web3.Storage returns CIDv1 (bafy..., base32). Both shapes are accepted.
"""

import io
import re
import sys

import requests
from PIL import Image

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Must mirror the evidence limit in app/api/ipfs.py.
EVIDENCE_LIMIT_BYTES = 10 * 1024 * 1024

# CIDv0 (base58: "Qm" + 44 chars) or CIDv1 (base32: "baf..." prefix).
CID_PATTERN = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-z2-7]{30,})$")

# Shared state between steps (filled in as the run progresses).
STATE = {"gateway_url": None}


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    kwargs.setdefault("timeout", 90)
    response = requests.request(method, f"{BASE_URL}{path}", **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def _test_png() -> io.BytesIO:
    """A small real PNG - the evidence document used for the happy path."""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (37, 99, 235)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Backend reachable and IPFS configured."""
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["ipfs_configured"], "IPFS API URL is not configured"
    print(
        f"   service={payload['service']} env={payload['environment']} "
        f"ipfs_configured={deps['ipfs_configured']}"
    )


def step_upload_evidence():
    """Happy path: small PNG -> HTTP 201 with a well-formed CID + gateway URL."""
    status, payload = api(
        "POST",
        "/ipfs/upload",
        files={"file": ("eduledger-evidence-test.png", _test_png(), "image/png")},
    )
    assert status == 201, f"/ipfs/upload returned {status}: {payload}"
    cid = payload.get("cid", "")
    assert CID_PATTERN.match(cid), f"not a valid CID in response: {payload}"
    expected_url = f"https://ipfs.io/ipfs/{cid}"
    assert payload.get("gateway_url") == expected_url, f"unexpected gateway_url: {payload}"
    STATE["gateway_url"] = expected_url
    origin = "local Kubo daemon (CIDv0)" if cid.startswith("Qm") else "Web3.Storage fallback (CIDv1)"
    print(f"   cid={cid}")
    print(f"   gateway_url={expected_url}")
    print(f"   note: CID shape suggests the upload was handled by the {origin}")


def step_gateway_reachable():
    """The returned gateway URL answers over HTTPS.

    Any HTTP response proves the gateway is reachable; a non-200 status is
    only a note here because content pinned to a local-only daemon may not
    be propagated to the public network yet (gateway discovery can lag).
    """
    url = STATE["gateway_url"]
    try:
        response = requests.get(url, timeout=(5, 30))
    except requests.RequestException as exc:
        raise AssertionError(f"gateway {url} is not reachable: {exc}") from exc
    note = ""
    if response.status_code != 200:
        note = (
            f" (status {response.status_code}: the CID may not be propagated to "
            "the public network yet - expected for local-daemon uploads)"
        )
    print(f"   gateway responded with HTTP {response.status_code}{note}")


def step_missing_file_rejected():
    """POST without a multipart 'file' field: rejected by request validation."""
    status, payload = api("POST", "/ipfs/upload", data={})
    assert status == 400, f"missing file must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "missing_fields", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_wrong_type_rejected():
    """A .txt upload: rejected by the evidence type policy."""
    status, payload = api(
        "POST",
        "/ipfs/upload",
        files={"file": ("notes.txt", io.BytesIO(b"not an evidence document"), "text/plain")},
    )
    assert status == 400, f"wrong type must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "invalid_file_type", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_oversized_rejected():
    """One byte over the 10 MB limit: rejected by the size policy."""
    oversized = io.BytesIO(b"\x89PNG" + b"0" * (EVIDENCE_LIMIT_BYTES - 3))  # 10 MB + 1
    status, payload = api(
        "POST",
        "/ipfs/upload",
        files={"file": ("oversized.png", oversized, "image/png")},
    )
    assert status == 400, f"oversized file must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "file_too_large", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health + IPFS configured", step_preflight),
    ("Upload small PNG -> CID + gateway URL", step_upload_evidence),
    ("Gateway URL is reachable", step_gateway_reachable),
    ("Missing file field -> blocked", step_missing_file_rejected),
    ("Wrong file type (.txt) -> blocked", step_wrong_type_rejected),
    ("Oversized file (10 MB + 1) -> blocked", step_oversized_rejected),
]


def main() -> int:
    print("=" * 60)
    print("   IPFS UPLOAD TESTS (test_ipfs_upload.py)")
    print("=" * 60)

    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            print("      start it first:  py -3.12 run.py   (from backend/)")
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
    print(f"   ALL IPFS UPLOAD TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
