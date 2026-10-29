"""Record-change proposal endpoints (Module 2 - Multi-Sig Anti-Tamper Audit Trail).

- POST /api/v1/records/propose        - instructor proposes a change; anchored
  on-chain (proposeChange) and mirrored into the local Proposal table
- POST /api/v1/records/<id>/approve   - HOD co-signs (HOD only, decision
  D2); the second signature auto-executes the change (RecordUpdated event)
- GET  /api/v1/records/<id>           - proposal details read straight from
  the contract (the chain is the source of truth, not the local mirror)
- GET  /api/v1/records/pending        - locally mirrored Pending proposals
  (the HOD / Exam Controller dashboard feed)

The Proposal table only mirrors on-chain state for fast queries; anything
stateful is read back from RecordAuditTrail. Because contracts are redeployed
frequently during development (and a fresh chain numbers proposals 1, 2, ...
again), the mirror is upserted by on-chain id: propose refreshes a leftover
row from a previous chain state instead of failing with a duplicate-key
error, and backend/reset_local_state.py clears the mirror for a clean slate
after a redeploy. Every failure response carries
{error, check, message} naming the failed stage, exactly like the Module 1
endpoints: "request" (input validation), "role" (role / signer gate),
"proposal" (proposal-state gate: not found, not pending, already signed),
"audit_trail" (chain unreachable or unconfigured), "database" (mirror sync).
"""

from flask import g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Proposal
from ..models.proposal import STATUS_PENDING, STATUS_REJECTED
from ..services import audit_trail
from ..services.auth import require_auth
from ..util import utcnow
from . import api_v1
from .errors import api_error

# AuditTrailError code -> (check, HTTP status) for the shared error shape.
_ERROR_RESPONSES = {
    "invalid_role": ("role", 400),
    "not_instructor": ("role", 403),
    "not_an_approver": ("role", 403),
    "proposal_not_found": ("proposal", 404),
    "proposal_not_pending": ("proposal", 409),
    "already_signed": ("proposal", 409),
    "empty_field": ("request", 400),
    "empty_ipfs_cid": ("request", 400),
}


def _audit_error(exc: audit_trail.AuditTrailError):
    """Map an AuditTrailError to the shared {error, check, message} shape."""
    check, status = _ERROR_RESPONSES.get(exc.code, ("audit_trail", 502))
    return api_error(check, exc.code, exc.message, status)


@api_v1.post("/records/propose")
@require_auth(roles=["FACULTY"])
def propose():
    """Propose a record change; anchor it on-chain and mirror it locally.

    Body: ``{"studentId": 2024001, "field": "grade", "oldValue": "B",
    "newValue": "A", "ipfsCid": "<from POST /api/v1/ipfs/upload>"}``.

    The on-chain proposeChange() runs first (from the instructor signer) so a
    failed transaction never leaves a local row; only then is the local
    Proposal mirror written. The mirror is upserted by on-chain id: when a
    leftover row from a previous chain deployment already occupies that id
    (contracts are redeployed often in development), it is refreshed to the
    new on-chain state instead of failing with a duplicate-key error.
    The logical proposer is the authenticated faculty user (g.user_id);
    any proposer id supplied in the body is ignored. The chain still uses
    the configured instructor signer. The current mirror has no user-proposer
    column, so proposer_id is returned here but is not persisted.
    """
    proposer_id = g.user_id
    body = request.get_json(silent=True) or {}
    student_id = body.get("studentId")
    field = body.get("field")
    old_value = body.get("oldValue", "")
    new_value = body.get("newValue", "")
    ipfs_cid = body.get("ipfsCid")

    if not isinstance(student_id, int) or student_id <= 0:
        return api_error("request", "missing_fields", "studentId (positive int) is required")
    if not isinstance(field, str) or not field.strip():
        return api_error("request", "missing_fields", "field (non-empty string) is required")
    if not isinstance(ipfs_cid, str) or not ipfs_cid.strip():
        return api_error(
            "request",
            "missing_fields",
            "ipfsCid (non-empty string) is required - pin the evidence via "
            "/api/v1/ipfs/upload first",
        )

    field = field.strip()
    # Course-specific grades must use the row-locking correction flow.
    # Other generic fields (including legacy opaque "grade") are unchanged.
    if field.startswith("grade:"):
        return api_error(
            "request", "use_grade_corrections",
            "course-specific grades must be proposed via /grades/corrections",
        )
    ipfs_cid = ipfs_cid.strip()
    old_value = str(old_value)
    new_value = str(new_value)

    try:
        result = audit_trail.propose_change(student_id, field, old_value, new_value, ipfs_cid)
    except audit_trail.AuditTrailError as exc:
        return _audit_error(exc)

    # Upsert the mirror keyed by the on-chain proposal id. A redeployed
    # contract numbers proposals 1, 2, ... again, so a leftover local row
    # from the previous chain state can already occupy this id. The chain is
    # the source of truth: refresh that row rather than fail the request.
    proposal = Proposal.query.filter_by(onchain_proposal_id=result["proposal_id"]).first()
    if proposal is None:
        proposal = Proposal(
            student_id=student_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            ipfs_cid=ipfs_cid,
            onchain_proposal_id=result["proposal_id"],
            status=result["status"],
        )
        db.session.add(proposal)
        mirror_action = "created"
    else:
        proposal.student_id = student_id
        proposal.field = field
        proposal.old_value = old_value
        proposal.new_value = new_value
        proposal.ipfs_cid = ipfs_cid
        proposal.status = result["status"]
        proposal.created_at = utcnow()  # the row now mirrors a NEW proposal
        mirror_action = "updated"
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_error(
            "database",
            "proposal_mirror_failed",
            f"proposal {result['proposal_id']} exists on-chain but could not be "
            f"mirrored locally: {exc}",
            502,
            onchain_proposal_id=result["proposal_id"],
            transaction_hash=result["transaction_hash"],
        )

    return (
        jsonify(
            {
                "status": proposal.status,
                "proposer_id": proposer_id,
                "proposal": proposal.to_dict(),
                "onchain_proposal_id": proposal.onchain_proposal_id,
                "mirror_action": mirror_action,
                "transaction_hash": result["transaction_hash"],
                "block_number": result["block_number"],
                "signer": result["signer"],
                "event": result["event"],
            }
        ),
        201,
    )


@api_v1.post("/records/<int:proposal_id>/approve")
@require_auth(roles=["HOD"])
def approve(proposal_id: int):
    """Co-sign using the authenticated HOD user's signer.

    Only HOD may approve (decision D2); ADMIN is rejected with 403 by the
    auth gate. The body is ignored: the signer role comes from the token
    (HOD maps to "hod"). When the signature count reaches 2, the unchanged
    contract auto-executes the change and emits RecordUpdated. The local
    Proposal mirror's status is synced to the on-chain result.

    Note the URL carries the ON-CHAIN proposal id (getProposal's id), not the
    local Proposal.id primary key.
    """
    role = {"HOD": "hod"}[g.role]

    # The generic route must not bypass a rejected grade correction or
    # execute one without applying its Grade row and releasing its lock.
    # Inspect chain-only proposals too, retaining the generic 404 behavior.
    mirror = Proposal.query.filter_by(onchain_proposal_id=proposal_id).first()
    if mirror is None:
        try:
            field = audit_trail.get_proposal(proposal_id)["field"]
        except audit_trail.AuditTrailError as exc:
            return _audit_error(exc)
    else:
        field = mirror.field
    if field.startswith("grade:"):
        if mirror is not None and mirror.status == STATUS_REJECTED:
            return api_error(
                "proposal", "correction_rejected",
                "this grade correction was rejected and cannot be approved", 409,
            )
        return api_error(
            "request", "use_grade_corrections",
            "grade corrections must be approved via /grades/corrections/<id>/approve",
        )

    try:
        result = audit_trail.approve_proposal(proposal_id, role)
    except audit_trail.AuditTrailError as exc:
        return _audit_error(exc)

    # Keep the local mirror in step with the on-chain result.
    if mirror is not None:
        mirror.status = result["status"]
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            return api_error(
                "database",
                "proposal_mirror_failed",
                f"approval succeeded on-chain but the local mirror could not be "
                f"updated: {exc}",
                502,
                onchain_proposal_id=proposal_id,
                onchain_status=result["status"],
                transaction_hash=result["transaction_hash"],
            )

    return jsonify(
        {
            "status": result["status"],
            "proposal_id": proposal_id,
            "transaction_hash": result["transaction_hash"],
            "block_number": result["block_number"],
            "signer": result["signer"],
            "role": result["role"],
            "signature_count": result["signature_count"],
            "executed_at": result["executed_at"],
            "record_updated": result["record_updated"],
            "event": result["event"],
            "local_record": mirror is not None,
        }
    )


@api_v1.get("/records/<int:proposal_id>")
@require_auth()
def get_record(proposal_id: int):
    """Current proposal details, read from the contract (source of truth).

    The local Proposal mirror is deliberately not consulted: a proposal that
    exists only on-chain (proposed by another client) is still returned in
    full, including its signature count and status.
    """
    try:
        proposal = audit_trail.get_proposal(proposal_id)
    except audit_trail.AuditTrailError as exc:
        return _audit_error(exc)
    return jsonify(proposal)


@api_v1.get("/records/pending")
@require_auth(roles=["FACULTY", "HOD", "ADMIN"])
def pending_records():
    """Locally mirrored proposals still Pending, for the approver dashboard.

    Reads the local Proposal table (by design - the mirror exists precisely
    so this listing does not need to scan chain history). Rows whose on-chain
    state has moved on are refreshed by the approve endpoint; the per-id GET
    /records/<id> endpoint is the authoritative view.
    """
    rows = (
        Proposal.query.filter_by(status=STATUS_PENDING)
        .order_by(Proposal.created_at.desc())
        .all()
    )
    return jsonify({"pending": [row.to_dict() for row in rows], "count": len(rows)})
