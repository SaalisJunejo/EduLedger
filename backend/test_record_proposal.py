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
     6. approve again          - rejected 409 proposal_not_pending   [proposal]
     7. approve bad role       - rejected 400 invalid_role           [role]
     8. unknown proposal       - rejected 404 proposal_not_found     [proposal]
     9. propose (leave open)   - 201: a second proposal stays Pending
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
"""

import sys

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

# Shared state between steps (filled in as the run progresses).
STATE = {"executed_id": None, "pending_id": None}


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    response = requests.request(method, f"{BASE_URL}{path}", timeout=15, **kwargs)
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
            "field": field,
            "oldValue": old_value,
            "newValue": new_value,
            "ipfsCid": cid,
        },
    )


def get_proposal(proposal_id: int) -> tuple[int, dict]:
    """GET /records/<id> - the contract-sourced (source-of-truth) view."""
    return api("GET", f"/records/{proposal_id}")


def approve(proposal_id: int, role: str) -> tuple[int, dict]:
    """POST /records/<id>/approve as the given role."""
    return api("POST", f"/records/{proposal_id}/approve", json={"role": role})


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    """Backend reachable and configured (DB + blockchain RPC + audit trail)."""
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
    status, payload = approve(proposal_id, "hod")
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
    """Approving an already-Executed proposal: rejected by the contract."""
    proposal_id = STATE["executed_id"]
    status, payload = approve(proposal_id, "exam_controller")
    assert status == 409, f"second approval must be rejected with 409, got {status}: {payload}"
    assert payload.get("error") == "proposal_not_pending", f"unexpected error: {payload}"
    assert payload.get("check") == "proposal", f"unexpected check: {payload}"
    print(f"   rejected at check '{payload['check']}': {payload['message']}")


def step_invalid_role_blocked():
    """An instructor trying to approve: rejected at the role gate."""
    proposal_id = STATE["executed_id"]
    status, payload = approve(proposal_id, "instructor")
    assert status == 400, f"invalid role must be rejected with 400, got {status}: {payload}"
    assert payload.get("error") == "invalid_role", f"unexpected error: {payload}"
    assert payload.get("check") == "role", f"unexpected check: {payload}"
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
    """/records/pending lists the open proposal and not the executed one."""
    status, payload = api("GET", "/records/pending")
    assert status == 200, f"GET /records/pending returned {status}: {payload}"
    entries = payload.get("pending") or []
    ids = [entry.get("onchainProposalId") for entry in entries]

    assert STATE["pending_id"] in ids, (
        f"open proposal {STATE['pending_id']} missing from /records/pending: {ids}"
    )
    assert STATE["executed_id"] not in ids, (
        f"executed proposal {STATE['executed_id']} must not be listed as pending: {ids}"
    )
    for entry in entries:
        assert entry.get("status") == "Pending", f"non-Pending row in the pending list: {entry}"
    assert payload.get("count") == len(entries), f"count mismatch: {payload}"
    print(f"   {payload.get('count')} pending proposal(s); includes {STATE['pending_id']}, excludes {STATE['executed_id']}")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Propose without evidence CID -> blocked", step_propose_missing_cid),
    ("Propose record change -> on-chain Pending proposal", step_propose_valid),
    ("Read proposal from chain -> Pending, 1 signature", step_read_pending_from_chain),
    ("Approve as HOD -> Executed + RecordUpdated event", step_approve_as_hod),
    ("Re-read from chain -> Executed, 2 signatures", step_read_executed_from_chain),
    ("Second approval attempt -> blocked, already executed", step_second_approval_blocked),
    ("Approval with invalid role -> blocked", step_invalid_role_blocked),
    ("Unknown proposal id -> blocked", step_unknown_proposal_blocked),
    ("Propose a second change (left Pending)", step_propose_second_left_pending),
    ("Pending list: open yes, executed no", step_pending_list),
]


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
