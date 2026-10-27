"""End-to-end test of the Phase 0.5 system admin bootstrap (GovernanceRegistry).

Run it against a live stack (from backend/):

    .\\venv\\Scripts\\python.exe test_governance.py

Prerequisites:
    - Hardhat node running          (contracts/: npm run node)
    - GovernanceRegistry deployed   (contracts/: npm run deploy:governance)
    - Flask backend running         (backend/: python run.py)
    - BOOTSTRAP_ADMIN_SECRET set in backend/.env (the test reads the same file
      the server does, so both sides see one secret)

Steps:
    0. preflight            - GET /health, plus the configured bootstrap secret
    1. status before        - GET /governance/status (public, no token)
    2. wrong secret         - rejected 403 invalid_bootstrap_secret
    3. bootstrap (success)  - lockRoot() on-chain, ADMIN created, tx returned
    4. bootstrap again      - rejected 409 root_already_locked
    5. status after         - locked, rootAdmin set, adminUserExists
    6. admin login + /auth/me - the new account authenticates as role ADMIN

The root lock is PERMANENT per chain, so step 3 can only run once per Hardhat
node. When the registry is already locked at startup, steps 3 and 6 print SKIP
and the rejection + status checks still run in full. The bootstrapped ADMIN
user is intentionally NOT deleted - it is the system root account.
"""

import os
import sys
from uuid import uuid4

import requests

os.environ["SCHEDULER_ENABLED"] = "false"

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

RUN_ID = uuid4().hex[:8]
ADMIN_EMAIL = f"admin.bootstrap.{RUN_ID}@gcu.edu.pk"
ADMIN_PASSWORD = "AdminBootstrap123!"
ADMIN_NAME = "System Admin (bootstrap test)"

# Filled in by step 0 / step 1.
CONFIG = {"secret": "", "expected_root": ""}
STATE = {
    "locked_before": None,
    "root_before": None,
    "created": False,
    "admin_token": None,
    "admin_id": None,
}


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=60, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def bootstrap(secret: str, email: str | None = None) -> tuple[int, dict]:
    """POST /auth/bootstrap/admin - deliberately unauthenticated (no ADMIN exists yet)."""
    return api(
        "POST",
        "/auth/bootstrap/admin",
        json={
            "full_name": ADMIN_NAME,
            "email": email or ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "bootstrap_secret": secret,
        },
    )


def governance_status() -> dict:
    """GET /governance/status, asserting the public contract of the payload."""
    status, payload = api("GET", "/governance/status")
    assert status == 200, f"/governance/status returned {status}: {payload}"
    for key in ("locked", "rootAdmin", "adminUserExists"):
        assert key in payload, f"status payload is missing {key!r}: {payload}"
    assert isinstance(payload["locked"], bool), f"locked is not a bool: {payload}"
    assert isinstance(payload["adminUserExists"], bool), f"adminUserExists is not a bool: {payload}"
    assert str(payload["rootAdmin"]).startswith("0x"), f"rootAdmin is not an address: {payload}"
    return payload


# --------------------------------------------------------------------------
# Steps (a step may return a string to report itself as skipped)
# --------------------------------------------------------------------------
def step_preflight():
    """Backend reachable and configured, and the bootstrap secret is readable."""
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"

    # The server loads backend/.env through app.config; read the same values so
    # the test never carries its own copy of the secret.
    from app import create_app

    config = create_app().config
    CONFIG["secret"] = str(config.get("BOOTSTRAP_ADMIN_SECRET") or "")
    CONFIG["expected_root"] = str(config.get("ADMIN_ROOT_ADDRESS") or "")
    assert CONFIG["secret"], (
        "BOOTSTRAP_ADMIN_SECRET is empty - add it to backend/.env "
        "(see .env.example) and restart the backend"
    )
    assert CONFIG["expected_root"].startswith("0x"), (
        f"ADMIN_ROOT_ADDRESS is not an address: {CONFIG['expected_root']!r}"
    )
    print(
        f"   service={payload['service']} env={payload['environment']} "
        f"root_address={CONFIG['expected_root']} secret_len={len(CONFIG['secret'])}"
    )


def step_status_before():
    """Public status read before any bootstrap attempt."""
    payload = governance_status()
    STATE["locked_before"] = payload["locked"]
    STATE["root_before"] = payload["rootAdmin"]
    if not payload["locked"]:
        assert payload["rootAdmin"] == ZERO_ADDRESS, (
            f"unlocked registry must report the zero root admin: {payload}"
        )
    print(
        f"   locked={payload['locked']} rootAdmin={payload['rootAdmin']} "
        f"adminUserExists={payload['adminUserExists']}"
    )


def step_wrong_secret_rejected():
    """A wrong bootstrap_secret is rejected before anything is read or written."""
    status, payload = bootstrap("definitely-not-the-bootstrap-secret")
    assert status == 403, f"wrong secret must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "invalid_bootstrap_secret", f"unexpected error: {payload}"
    assert payload.get("check") == "bootstrap", f"unexpected check: {payload}"
    assert payload.get("message"), f"no message in error payload: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_bootstrap_success():
    """The happy path: lockRoot() on-chain, then the ADMIN account."""
    if STATE["locked_before"]:
        return (
            "the root is already locked on this chain "
            f"({STATE['root_before']}) - the lock is permanent per chain"
        )

    status, payload = bootstrap(CONFIG["secret"])
    assert status == 201, f"bootstrap returned {status}: {payload}"
    tx_hash = payload.get("transactionHash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transactionHash: {payload}"
    assert payload.get("locked") is True, f"response does not report the lock: {payload}"
    assert payload["rootAdmin"].lower() == CONFIG["expected_root"].lower(), (
        f"locked root {payload['rootAdmin']} != ADMIN_ROOT_ADDRESS {CONFIG['expected_root']}"
    )
    user = payload["user"]
    assert user["role"] == "ADMIN", f"bootstrapped user is not ADMIN: {user}"
    assert user["email"] == ADMIN_EMAIL, f"unexpected admin email: {user}"
    assert payload.get("created") is True, f"a new ADMIN should have been created: {payload}"

    STATE["created"] = True
    STATE["admin_id"] = user["id"]
    print(
        f"   root locked: tx={tx_hash} block={payload['blockNumber']} "
        f"rootAdmin={payload['rootAdmin']} signer={payload['signer']} admin_id={user['id']}"
    )


def step_second_bootstrap_rejected():
    """The lock is one-shot: a second bootstrap is refused (409)."""
    status, payload = bootstrap(CONFIG["secret"], email=f"admin.second.{RUN_ID}@gcu.edu.pk")
    assert status == 409, f"second bootstrap must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "root_already_locked", f"unexpected error: {payload}"
    assert payload.get("check") == "governance", f"unexpected check: {payload}"
    assert payload.get("message"), f"no message in error payload: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_status_after():
    """Status now reports a locked root with a non-zero admin address."""
    payload = governance_status()
    assert payload["locked"] is True, f"registry should be locked by now: {payload}"
    assert payload["rootAdmin"] != ZERO_ADDRESS, f"locked registry has a zero root: {payload}"
    if STATE["created"]:
        assert payload["rootAdmin"].lower() == CONFIG["expected_root"].lower(), (
            f"rootAdmin {payload['rootAdmin']} != ADMIN_ROOT_ADDRESS {CONFIG['expected_root']}"
        )
        assert payload["adminUserExists"] is True, (
            f"an ADMIN user was created but status says otherwise: {payload}"
        )
    print(
        f"   locked={payload['locked']} rootAdmin={payload['rootAdmin']} "
        f"adminUserExists={payload['adminUserExists']}"
    )


def step_admin_login():
    """The bootstrapped ADMIN logs in and /auth/me reports role ADMIN."""
    if not STATE["created"]:
        return "no ADMIN was created in this run (success path skipped)"

    status, payload = api("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert status == 200, f"admin login failed ({status}): {payload}"
    token = payload.get("token")
    assert token, f"no token in login response: {payload}"
    assert payload["user"]["role"] == "ADMIN", f"login reports a non-ADMIN role: {payload['user']}"
    STATE["admin_token"] = token

    status, me = api("GET", "/auth/me", token=token)
    assert status == 200, f"/auth/me returned {status}: {me}"
    assert me["user"]["role"] == "ADMIN", f"/auth/me reports a non-ADMIN role: {me['user']}"
    assert me["user"]["id"] == STATE["admin_id"], f"/auth/me returned another user: {me['user']}"
    assert me["user"]["email"] == ADMIN_EMAIL, f"/auth/me returned another email: {me['user']}"
    print(f"   authenticated admin_id={me['user']['id']} role={me['user']['role']}")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health + bootstrap config", step_preflight),
    ("Governance status before bootstrap (public)", step_status_before),
    ("Bootstrap with a wrong secret -> blocked", step_wrong_secret_rejected),
    ("Bootstrap admin -> root locked on-chain", step_bootstrap_success),
    ("Bootstrap again -> blocked, root already locked", step_second_bootstrap_rejected),
    ("Governance status after bootstrap (public)", step_status_after),
    ("Admin login + /auth/me reports ADMIN", step_admin_login),
]


def main() -> int:
    print("=" * 60)
    print("   SYSTEM ADMIN BOOTSTRAP TESTS (test_governance.py)")
    print("=" * 60)
    print(f"   run id: {RUN_ID}   admin email: {ADMIN_EMAIL}")

    skipped = 0
    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            outcome = action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            print("      start it first:  .\\venv\\Scripts\\python.exe run.py   (from backend/)")
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
        except Exception as exc:
            print(f"   \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
            print("\n" + "=" * 60)
            print(f"   TEST RUN FAILED AT STEP {index}: {title}")
            print("=" * 60)
            return 1
        if outcome:
            skipped += 1
            print(f"   \u23ed SKIP: {outcome}")
        else:
            print("   \u2705 PASS")

    print("\n" + "=" * 60)
    passed = len(STEPS) - skipped
    if skipped:
        print(f"   ALL GOVERNANCE CHECKS PASSED ({passed} steps, {skipped} skipped)")
        print("   restart the Hardhat node and redeploy to exercise the skipped steps")
    else:
        print(f"   ALL GOVERNANCE TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
