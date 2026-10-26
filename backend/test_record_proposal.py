"""End-to-end test of the Module 2 record-change proposal + approval flow.

Run it against a live stack (from backend/):

    py -3.12 test_record_proposal.py

Prerequisites:
    - Hardhat node running          (contracts/: npm run node)
    - RecordAuditTrail deployed     (contracts/: npm run deploy:audit-trail)
    - Flask backend running         (backend/: py -3.12 run.py)

Steps (each prints a clear PASS/FAIL line; the first failure stops the run
with the failing response, and the script exits non-zero - no uncaught
exceptions):

     0. preflight              - GET /health (backend reachable, DB + RPC +
                                  audit-trail contract configured)
     1. propose (no CID)       - rejected 400 missing_fields         [request]
     2. propose (valid)        - 201: Pending proposal on-chain + local
                                  mirror, signed by the Instructor account
     3. read from chain        - GET /records/<id>: Pending, 1 signature
     4. approve as HOD         - 200: Executed, real tx hash + RecordUpdated
                                  event data
     5. re-read from chain     - GET /records/<id>: Executed, 2 signatures
     6. ADMIN approval         - executed proposal rejected 409; fresh proposal
                                  executes via exam_controller, ignoring body role
     7. FACULTY approval       - rejected 403 insufficient_role      [auth]
     8. unknown proposal       - rejected 404 proposal_not_found     [proposal]
     9. propose (leave open)   - 201: another proposal stays Pending
    10. pending list           - GET /records/pending contains the open
                                  proposal and excludes the executed one

API contract notes (aligned with app/api/records.py):
    - proposal ids in URLs are the ON-CHAIN ids (getProposal's id), not the
      local Proposal.id primary key.
    - statuses are the contract's enum names: "Pending" / "Executed".
    - the ipfsCid is normally obtained from POST /api/v1/ipfs/upload; the
      contract stores it opaquely, so this test uses fixed CID constants to
      stay independent of the IPFS daemon.
    - studentId is not FK-checked against the students table: the audit
      trail covers any student id, not only those enrolled in Module 1.
    - preflight includes inline registration/login with unique emails; the
      HOD whitelist entry and directly created ADMIN are temporary fixtures.
    - protected requests carry real login tokens; registration/login bootstrap
      requests are public. Approval ignores the body role.
"""

import os
import sys
from uuid import uuid4

import requests

# Windows consoles often default to a legacy code page (cp1252) that cannot
# print the status marks below; force UTF-8 so the run never dies on a print.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"

# Standard test inputs. studentId needs no students-table row (see notes
# above); the CIDs stand in for evidence pinned via /api/v1/ipfs/upload.
STUDENT_ID = 2024001
CID_GRADE = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
CID_ATTENDANCE = "bafybeigvfueidq2nncr6wvq4xd7vybumsrdhktjdvcb3hphqof7rqgd4m4"

# Role accounts from contracts/deployed/accounts.json - hardcoded so the run
# also proves the configured signers really are the labeled accounts.
INSTRUCTOR_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
HOD_ADDRESS = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
EXAM_CONTROLLER_ADDRESS = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"

# Shared state between steps (filled in as the run progresses).
STATE = {
    "executed_id": None, "pending_id": None, "admin_executed_id": None,
    "db_app": None, "fixture_emails": [], "whitelist_email": None,
}
AUTH = {}
RUN_ID = uuid4().hex


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Call the API with a login token, except during public auth bootstrap."""
    token = token or AUTH.get("faculty_token")
    if not path.startswith("/auth/"):
        assert token, f"no login token available for {method} {path}"
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=15, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def propose(field: str, old_value: str, new_value: str, cid: str) -> tuple[int, dict]:
    """POST /records/propose with the standard student id."""
    return api(
        "POST",
        "/records/propose",
        json={
            "studentId": STUDENT_ID,
            "proposerId": AUTH["admin_id"],  # spoofed id must be ignored
            "field": field,
            "oldValue": old_value,
            "newValue": new_value,
            "ipfsCid": cid,
        },
    )


def get_proposal(proposal_id: int) -> tuple[int, dict]:
    """GET /records/<id> - the contract-sourced (source-of-truth) view."""
    return api("GET", f"/records/{proposal_id}")


def approve(proposal_id: int, user_role: str, body_role: str) -> tuple[int, dict]:
    """Approve using the selected user's token and an untrusted body role."""
    return api(
        "POST", f"/records/{proposal_id}/approve",
        token=AUTH[f"{user_role}_token"], json={"role": body_role},
    )


def setup_auth():
    """Register FACULTY/HOD inline and log in a temporary DB-created ADMIN."""
    # This one-off DB fixture app must never run background chain-writing jobs.
    os.environ["SCHEDULER_ENABLED"] = "false"
    from app import create_app
    from app.extensions import db
    from app.models import InstitutionWhitelist, User, UserRole
    from app.services.auth import hash_password

    app = create_app()
    STATE["db_app"] = app
    domain = app.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk")
    password = "RecordTestPassword123!"
    accounts = {
        role: {
            "email": f"records.{role}.{RUN_ID}@{domain}",
            "password": password,
            "full_name": f"Record Test {role.upper()}",
            "department": "Computer Science",
            "employee_id": f"REC-{role.upper()}-{RUN_ID}",
        }
        for role in ("faculty", "hod", "admin")
    }
    STATE["fixture_emails"] = [account["email"] for account in accounts.values()]
    STATE["whitelist_email"] = accounts["hod"]["email"]
    with app.app_context():
        hod = accounts["hod"]
        db.session.add(InstitutionWhitelist(
            employee_id=hod["employee_id"], email=hod["email"],
            name=hod["full_name"], department=hod["department"],
        ))
        admin = accounts["admin"]
        db.session.add(User(
            role=UserRole.ADMIN, email=admin["email"],
            password_hash=hash_password(password), full_name=admin["full_name"],
            department=None, is_active=True,
        ))
        db.session.commit()

    for role, account in accounts.items():
        if role != "admin":
            status, payload = api("POST", f"/auth/register/{role}", json=account)
            assert status == 201, f"{role} registration failed ({status}): {payload}"
            assert payload["user"]["role"] == role.upper(), f"wrong registration role: {payload}"
        status, payload = api(
            "POST", "/auth/login",
            json={"email": account["email"], "password": password},
        )
        assert status == 200, f"{role} login failed ({status}): {payload}"
        assert payload["user"]["role"] == role.upper(), f"wrong login role: {payload}"
        assert payload.get("token"), f"{role} login returned no token: {payload}"
        AUTH[f"{role}_token"] = payload["token"]
        AUTH[f"{role}_id"] = payload["user"]["id"]
    print(
        f"   authenticated faculty_id={AUTH['faculty_id']} "
        f"hod_id={AUTH['hod_id']} admin_id={AUTH['admin_id']}"
    )


def cleanup_auth():
    """Delete only this run's user fixtures (including ADMIN) and HOD whitelist."""
    app = STATE["db_app"]
    if app is None:
        return
    from app.extensions import db
    from app.models import InstitutionWhitelist, User

    with app.app_context():
        for user in User.query.filter(User.email.in_(STATE["fixture_emails"])).all():
            db.session.delete(user)
        entry = InstitutionWhitelist.query.filter_by(email=STATE["whitelist_email"]).first()
        if entry is not None:
            db.session.delete(entry)
        db.session.commit()
    print("   temporary auth fixtures removed (including ADMIN)")


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Authenticate inline, then check DB + blockchain RPC + audit trail."""
    setup_auth()
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    assert deps["contracts_configured"]["audit_trail"], (
        "audit trail contract address is not configured (run `npm run deploy:audit-trail`)"
    )
    print(
        f"   service={payload['service']} env={payload['environment']} "
        f"db={deps['database_configured']} rpc={deps['blockchain_rpc_configured']} "
        f"audit_trail_contract={deps['contracts_configured']['audit_trail']}"
    )


def step_propose_missing_cid():
    """A proposal without the evidence CID never reaches the chain."""
    status, payload = api(
        "POST",
        "/records/propose",
        json={"studentId": STUDENT_ID, "field": "grade", "oldValue": "B", "newValue": "A"},
    )
    assert status == 400, f"missing-CID propose must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "missing_fields", f"unexpected error: {payload}"
    assert payload.get("check") == "request", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_propose_valid():
    """The happy propose: on-chain Pending proposal + local mirror row."""
    status, payload = propose("grade", "B", "A", CID_GRADE)
    assert status == 201, f"propose returned {status}: {payload}"
    assert payload.get("status") == "Pending", f"new proposal is not Pending: {payload}"
    assert payload.get("proposer_id") == AUTH["faculty_id"], (
        f"proposer must come from the FACULTY token, not the spoofed body id: {payload}"
    )
    assert payload["proposal"]["studentId"] == STUDENT_ID, f"target student changed: {payload}"

    onchain_id = payload.get("onchain_proposal_id")
    assert isinstance(onchain_id, int) and onchain_id >= 1, f"bad onchain_proposal_id: {payload}"

    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"

    event = payload.get("event") or {}
    assert str(event.get("proposer", "")).lower() == INSTRUCTOR_ADDRESS.lower(), (
        f"proposer is not the Instructor account: {event}"
    )
    assert event.get("field") == "grade" and event.get("oldValue") == "B", f"event mismatch: {event}"
    assert event.get("newValue") == "A" and event.get("ipfsCid") == CID_GRADE, f"event mismatch: {event}"

    proposal = payload.get("proposal") or {}
    assert proposal.get("onchainProposalId") == onchain_id, f"mirror points at another proposal: {proposal}"
    assert proposal.get("status") == "Pending", f"mirror status wrong: {proposal}"

    STATE["executed_id"] = onchain_id
    print(f"   proposal {onchain_id} on-chain: tx={tx_hash} proposer={event.get('proposer')}")


def step_read_pending_from_chain():
    """Source-of-truth read: the new proposal is Pending with 1 signature."""
    proposal_id = STATE["executed_id"]
    status, payload = get_proposal(proposal_id)
    assert status == 200, f"GET /records/{proposal_id} returned {status}: {payload}"
    assert payload.get("proposalId") == proposal_id, f"wrong proposal id: {payload}"
    assert payload.get("status") == "Pending", f"expected Pending, got {payload}"
    assert payload.get("signatureCount") == 1, f"expected 1 signature, got {payload}"
    assert str(payload.get("proposer", "")).lower() == INSTRUCTOR_ADDRESS.lower(), (
        f"unexpected proposer: {payload}"
    )
    assert payload.get("field") == "grade" and payload.get("ipfsCid") == CID_GRADE, (
        f"unexpected proposal contents: {payload}"
    )
    print(f"   proposal {payload['proposalId']}: status={payload['status']} signatures={payload['signatureCount']}")


def step_approve_as_hod():
    """The happy approve: HOD co-signs -> auto-execute -> RecordUpdated event."""
    proposal_id = STATE["executed_id"]
    status, payload = approve(proposal_id, "hod", "exam_controller")
    assert status == 200, f"approve returned {status}: {payload}"
    assert payload.get("status") == "Executed", f"proposal did not execute: {payload}"

    tx_hash = payload.get("transaction_hash")
    assert tx_hash and str(tx_hash).startswith("0x"), f"bad transaction_hash: {payload}"
    assert payload.get("signature_count") == 2, f"expected 2 signatures, got {payload}"
    assert str(payload.get("signer", "")).lower() == HOD_ADDRESS.lower(), (
        f"approve signer is not the HOD account: {payload}"
    )
    assert payload.get("executed_at"), f"no executed_at: {payload}"
    assert payload.get("record_updated") is True, f"RecordUpdated was not emitted: {payload}"
    assert payload.get("role") == "hod", f"unexpected role in response: {payload}"

    event = payload.get("event") or {}
    assert event.get("studentId") == STUDENT_ID and event.get("field") == "grade", f"event mismatch: {event}"
    assert event.get("oldValue") == "B" and event.get("newValue") == "A", f"event mismatch: {event}"
    assert event.get("ipfsCid") == CID_GRADE, f"event mismatch: {event}"
    assert isinstance(event.get("timestamp"), int) and event["timestamp"] > 0, (
        f"bad RecordUpdated timestamp: {event}"
    )
    print(f"   executed: tx={tx_hash} signatures={payload['signature_count']} executed_at={payload['executed_at']}")


def step_read_executed_from_chain():
    """Source-of-truth read after execution: Executed with 2 signatures."""
    proposal_id = STATE["executed_id"]
    status, payload = get_proposal(proposal_id)
    assert status == 200, f"GET /records/{proposal_id} returned {status}: {payload}"
    assert payload.get("status") == "Executed", f"expected Executed, got {payload}"
    assert payload.get("signatureCount") == 2, f"expected 2 signatures, got {payload}"
    assert payload.get("executedAt"), f"executedAt not set: {payload}"
    print(f"   proposal {payload['proposalId']}: status={payload['status']} signatures={payload['signatureCount']}")


def step_second_approval_blocked():
    """ADMIN cannot re-approve an executed proposal, but can execute a fresh one."""
    proposal_id = STATE["executed_id"]
    status, payload = approve(proposal_id, "admin", "hod")
    assert status == 409, f"second approval must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "proposal_not_pending", f"unexpected error: {payload}"
    assert payload.get("check") == "proposal", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")

    status, payload = propose("grade", "C", "B", CID_GRADE)
    assert status == 201, f"ADMIN-path proposal returned {status}: {payload}"
    admin_proposal_id = payload["onchain_proposal_id"]
    STATE["admin_executed_id"] = admin_proposal_id
    status, payload = approve(admin_proposal_id, "admin", "hod")
    assert status == 200, f"ADMIN approval returned {status}: {payload}"
    assert payload.get("role") == "exam_controller", f"ADMIN body role was trusted: {payload}"
    assert str(payload.get("signer", "")).lower() == EXAM_CONTROLLER_ADDRESS.lower(), (
        f"ADMIN must use the exam-controller signer: {payload}"
    )
    assert payload.get("status") == "Executed", f"ADMIN proposal did not execute: {payload}"
    assert payload.get("signature_count") == 2, f"expected 2 signatures: {payload}"
    assert payload.get("record_updated") is True, f"RecordUpdated missing: {payload}"
    assert str(payload.get("transaction_hash", "")).startswith("0x"), f"bad tx hash: {payload}"
    status, chain = get_proposal(admin_proposal_id)
    assert status == 200 and chain.get("status") == "Executed", f"ADMIN chain state: {chain}"
    assert chain.get("signatureCount") == 2, f"ADMIN chain signatures: {chain}"
    print(f"   ADMIN executed proposal {admin_proposal_id} via exam_controller (body role ignored)")


def step_invalid_role_blocked():
    """A FACULTY token spoofing an HOD body role: rejected at the auth gate."""
    proposal_id = STATE["executed_id"]
    status, payload = approve(proposal_id, "faculty", "hod")
    assert status == 403, f"FACULTY must be rejected with 403, got {status}: {payload}"
    assert payload.get("error") == "insufficient_role", f"unexpected error: {payload}"
    assert payload.get("check") == "auth", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_unknown_proposal_blocked():
    """An id that never existed: rejected with the contract's ProposalNotFound."""
    status, payload = get_proposal(999_999)
    assert status == 404, f"unknown proposal must be rejected with 404, got {status}: {payload}"
    assert payload.get("error") == "proposal_not_found", f"unexpected error: {payload}"
    assert payload.get("check") == "proposal", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_propose_second_left_pending():
    """A second proposal that stays Pending (feeds the pending-list check)."""
    status, payload = propose("attendance", "70", "75", CID_ATTENDANCE)
    assert status == 201, f"propose returned {status}: {payload}"
    assert payload.get("status") == "Pending", f"proposal is not Pending: {payload}"
    STATE["pending_id"] = payload.get("onchain_proposal_id")
    assert isinstance(STATE["pending_id"], int), f"bad onchain_proposal_id: {payload}"
    print(f"   proposal {STATE['pending_id']} on-chain and left Pending")


def step_pending_list():
    """FACULTY/HOD/ADMIN can list the open proposal, excluding executed ones."""
    for role in ("faculty", "hod", "admin"):
        status, payload = api("GET", "/records/pending", token=AUTH[f"{role}_token"])
        assert status == 200, f"{role} GET /records/pending returned {status}: {payload}"
        entries = payload.get("pending") or []
        ids = [entry.get("onchainProposalId") for entry in entries]
        assert STATE["pending_id"] in ids, (
            f"open proposal {STATE['pending_id']} missing from /records/pending: {ids}"
        )
        for executed_id in (STATE["executed_id"], STATE["admin_executed_id"]):
            assert executed_id not in ids, f"executed proposal {executed_id} listed as pending: {ids}"
        for entry in entries:
            assert entry.get("status") == "Pending", f"non-Pending row in the pending list: {entry}"
        assert payload.get("count") == len(entries), f"count mismatch: {payload}"
        print(f"   {role}: {payload['count']} pending proposal(s); includes {STATE['pending_id']}")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: register/login real users + backend /health", step_preflight),
    ("Propose without evidence CID -> blocked", step_propose_missing_cid),
    ("Propose record change -> on-chain Pending proposal", step_propose_valid),
    ("Read proposal from chain -> Pending, 1 signature", step_read_pending_from_chain),
    ("Approve as HOD -> Executed + RecordUpdated event", step_approve_as_hod),
    ("Re-read from chain -> Executed, 2 signatures", step_read_executed_from_chain),
    ("ADMIN: re-approval blocked; fresh proposal executes via exam_controller", step_second_approval_blocked),
    ("FACULTY spoofing HOD role -> blocked with 403", step_invalid_role_blocked),
    ("Unknown proposal id -> blocked", step_unknown_proposal_blocked),
    ("Propose a second change (left Pending)", step_propose_second_left_pending),
    ("Pending list: open yes, executed no", step_pending_list),
]


def _run_steps() -> int:
    print("=" * 60)
    print("   RECORD PROPOSAL & APPROVAL TESTS (test_record_proposal.py)")
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
    print(f"   ALL RECORD PROPOSAL TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0


def main() -> int:
    result = 1
    try:
        result = _run_steps()
    finally:
        try:
            cleanup_auth()
        except Exception as exc:
            print(f"   \u274c FAIL: auth fixture cleanup: {type(exc).__name__}: {exc}")
            result = 1
    return result


if __name__ == "__main__":
    sys.exit(main())
