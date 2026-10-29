"""Post-deadline grade corrections (Phase 1.4a - requirements doc section 5,
PRD v2 4.3, decisions D2 and D3).

- POST /api/v1/grades/corrections                  - FACULTY proposes a new
  grade; locks the Grade row and anchors the proposal on RecordAuditTrail
- POST /api/v1/grades/corrections/<id>/approve     - HOD co-signs on-chain
  (signature 2 executes the unchanged contract) and the grade is written
- POST /api/v1/grades/corrections/<id>/reject      - HOD rejects, off-chain
  only: the lock is released, the grade keeps its value
- GET  /api/v1/grades/corrections                  - FACULTY: their own;
  HOD and ADMIN: every correction
- GET  /api/v1/grades/<student_id>                 - a student's grades
  (a STUDENT may read only their own)

A correction is an ordinary RecordAuditTrail proposal with field
"grade:<course_code>" and the old / new grade strings as values, mirrored in
the Proposal table like /records proposals, plus the proposing user, the
justification and (after a reject) the reason. The justification is also
pinned to IPFS as JSON and its CID is the on-chain evidence. <id> in the
URLs is the ON-CHAIN proposal id, exactly like /records/<id>/approve.

Ordering rules: the chain is written first and the DB is changed only after
the chain call succeeded, so a failed transaction never leaves a lock (on
propose) or a changed grade (on approve). The Grade row (propose) or the
Proposal row (approve, reject) is held with SELECT ... FOR UPDATE for the
whole request, which serializes concurrent proposals on one grade and an
approve racing a reject.
"""

import json
import re

from flask import g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Grade, Proposal, User
from ..models.proposal import STATUS_EXECUTED, STATUS_PENDING, STATUS_REJECTED
from ..services import audit_trail
from ..services.auth import require_auth
from ..services.ipfs import IPFSUploadError, upload_to_ipfs
from ..util import iso_z, utcnow
from . import api_v1
from .errors import api_error
from .records import _audit_error

GRADE_FIELD_PREFIX = "grade:"
MIN_JUSTIFICATION_CHARS = 10
# Column sizes of Grade.course_code / Grade.grade_value.
MAX_COURSE_CODE_CHARS = 64
MAX_GRADE_CHARS = 16

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2147483647


def _correction_dict(proposal: Proposal, proposer: User | None) -> dict:
    """One grade correction as the list and action responses show it."""
    return {
        "id": proposal.id,
        "onchainProposalId": proposal.onchain_proposal_id,
        "studentId": proposal.student_id,
        "courseCode": proposal.field[len(GRADE_FIELD_PREFIX):],
        "oldGrade": proposal.old_value,
        "newGrade": proposal.new_value,
        "justification": proposal.justification,
        "ipfsCid": proposal.ipfs_cid,
        "status": proposal.status,
        "proposerId": proposal.proposer_id,
        "proposerName": proposer.full_name if proposer is not None else None,
        "rejectionReason": proposal.rejection_reason,
        "createdAt": iso_z(proposal.created_at),
    }


def _locked_correction(proposal_id: int) -> Proposal | None:
    """The grade-correction mirror row for an on-chain id, held FOR UPDATE."""
    return (
        Proposal.query.filter(
            Proposal.onchain_proposal_id == proposal_id,
            Proposal.field.startswith(GRADE_FIELD_PREFIX),
            Proposal.proposer_id.isnot(None),
        )
        .with_for_update()
        .first()
    )


def _closed_correction_error(proposal: Proposal):
    """409 for a correction that is no longer Pending (None while it is)."""
    if proposal.status == STATUS_REJECTED:
        return api_error(
            "proposal",
            "correction_rejected",
            f"grade correction {proposal.onchain_proposal_id} was rejected by the HOD "
            "and can no longer be approved or rejected",
            409,
        )
    if proposal.status != STATUS_PENDING:
        return api_error(
            "proposal",
            "proposal_not_pending",
            f"grade correction {proposal.onchain_proposal_id} is already {proposal.status}",
            409,
        )
    return None


@api_v1.post("/grades/corrections")
@require_auth(roles=["FACULTY"])
def propose_grade_correction():
    """Propose a grade correction; the Grade row stays locked until the HOD decides.

    Body: ``{"student_id": 12, "course_code": "BSCS-401", "new_grade": "A-",
    "justification": "Re-marked question 4 after the script review."}``.
    The proposer is the authenticated FACULTY user (g.user_id).
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_error("request", "missing_fields", "a JSON object is required")
    student_id = body.get("student_id")
    course_code = body.get("course_code")
    new_grade = body.get("new_grade")
    justification = body.get("justification")

    if not _is_positive_int(student_id):
        return api_error("request", "missing_fields", "student_id (positive int) is required")
    if not isinstance(course_code, str) or not course_code.strip():
        return api_error("request", "missing_fields", "course_code (non-empty string) is required")
    if not isinstance(new_grade, str) or not new_grade.strip():
        return api_error("request", "missing_fields", "new_grade (non-empty string) is required")
    course_code = course_code.strip()
    new_grade = new_grade.strip()
    if len(course_code) > MAX_COURSE_CODE_CHARS:
        return api_error(
            "request", "invalid_course_code",
            f"course_code must be at most {MAX_COURSE_CODE_CHARS} characters",
        )
    if len(new_grade) > MAX_GRADE_CHARS:
        return api_error(
            "request", "invalid_grade", f"new_grade must be at most {MAX_GRADE_CHARS} characters",
        )
    justification = justification.strip() if isinstance(justification, str) else ""
    if len(justification) < MIN_JUSTIFICATION_CHARS:
        return api_error(
            "request", "justification_too_short",
            f"justification must be at least {MIN_JUSTIFICATION_CHARS} characters",
        )

    # One transaction from here on: the row lock is held through the IPFS
    # pin and the chain call, so a concurrent proposal for the same grade
    # waits and then sees the lock this request sets.
    grade = (
        Grade.query.filter_by(student_id=student_id, course_code=course_code)
        .with_for_update()
        .first()
    )
    if grade is None:
        db.session.rollback()
        return api_error(
            "grade", "grade_not_found",
            f"student {student_id} has no grade for {course_code}", 404,
        )
    if grade.locked_by_proposal_id is not None:
        locked_by = grade.locked_by_proposal_id
        db.session.rollback()
        return api_error(
            "row_lock", "grade_locked",
            f"the {course_code} grade of student {student_id} already has a pending "
            "correction - wait for the HOD to approve or reject it",
            409,
            locked_by_proposal_id=locked_by,
        )
    old_grade = grade.grade_value
    if new_grade == old_grade:
        db.session.rollback()
        return api_error(
            "request", "grade_unchanged", f"the current grade is already {old_grade!r}",
        )

    proposer_id = g.user_id
    evidence = {
        "student_id": student_id,
        "course_code": course_code,
        "old_grade": old_grade,
        "new_grade": new_grade,
        "justification": justification,
        "proposer": proposer_id,
        "timestamp": iso_z(utcnow()),
    }
    safe_course = _UNSAFE_FILENAME_CHARS.sub("_", course_code)
    try:
        ipfs_cid = upload_to_ipfs(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            f"grade-correction-{student_id}-{safe_course}.json",
        )
    except IPFSUploadError as exc:
        db.session.rollback()
        return api_error("ipfs", exc.code, exc.message, 502)

    field = f"{GRADE_FIELD_PREFIX}{course_code}"
    try:
        result = audit_trail.propose_change(student_id, field, old_grade, new_grade, ipfs_cid)
    except audit_trail.AuditTrailError as exc:
        db.session.rollback()  # nothing was locked
        return _audit_error(exc)

    # Do not overwrite an existing mirror on an on-chain id collision:
    # another Grade may still reference that local row after a redeploy.
    # The unique key fails safely below, retaining the chain receipt and
    # leaving every existing lock untouched for manual reconciliation.
    proposal = Proposal(
        onchain_proposal_id=result["proposal_id"], student_id=student_id,
        field=field, old_value=old_grade, new_value=new_grade, ipfs_cid=ipfs_cid,
        status=result["status"], proposer_id=proposer_id,
        justification=justification,
    )
    try:
        db.session.add(proposal)
        db.session.flush()  # assigns proposal.id for a new row
        grade.locked_by_proposal_id = proposal.id
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_error(
            "database",
            "proposal_mirror_failed",
            f"correction {result['proposal_id']} exists on-chain but could not be "
            f"mirrored locally, so the grade was not locked: {exc}",
            502,
            onchain_proposal_id=result["proposal_id"],
            transaction_hash=result["transaction_hash"],
        )

    proposer = db.session.get(User, proposer_id)
    return (
        jsonify(
            {
                "status": proposal.status,
                "correction": _correction_dict(proposal, proposer),
                "grade": grade.to_dict(),
                "onchain_proposal_id": proposal.onchain_proposal_id,
                "mirror_action": "created",
                "ipfs_cid": ipfs_cid,
                "transaction_hash": result["transaction_hash"],
                "block_number": result["block_number"],
                "signer": result["signer"],
            }
        ),
        201,
    )


@api_v1.post("/grades/corrections/<int:proposal_id>/approve")
@require_auth(roles=["HOD"])
def approve_grade_correction(proposal_id: int):
    """HOD co-signs on-chain; on execution the new grade is written and unlocked.

    The signer role comes from the token (HOD -> the "hod" signer); the body
    is ignored. Proposal = signature 1, HOD = signature 2 (decision D2), so
    the approval executes the unchanged 2-of-3 contract.
    """
    signer_role = {"HOD": "hod"}[g.role]

    proposal = _locked_correction(proposal_id)
    if proposal is None:
        db.session.rollback()
        return api_error(
            "proposal", "correction_not_found",
            f"no grade correction with on-chain id {proposal_id}", 404,
        )
    closed = _closed_correction_error(proposal)
    if closed is not None:
        db.session.rollback()
        return closed
    course_code = proposal.field[len(GRADE_FIELD_PREFIX):]
    grade = (
        Grade.query.filter_by(
            locked_by_proposal_id=proposal.id,
            student_id=proposal.student_id,
            course_code=course_code,
        )
        .with_for_update()
        .first()
    )
    if grade is None:
        db.session.rollback()
        return api_error(
            "row_lock", "lock_missing",
            f"no grade row is locked by correction {proposal_id} - it cannot be applied",
            409,
        )

    try:
        result = audit_trail.approve_proposal(proposal_id, signer_role)
    except audit_trail.AuditTrailError as exc:
        db.session.rollback()  # grade and lock unchanged
        return _audit_error(exc)

    if result["status"] != STATUS_EXECUTED:
        db.session.rollback()
        return api_error(
            "audit_trail", "not_executed",
            f"the HOD signature was recorded but correction {proposal_id} did not "
            "execute on-chain; the grade is unchanged and stays locked",
            502,
            onchain_status=result["status"],
            signature_count=result["signature_count"],
            transaction_hash=result["transaction_hash"],
        )

    grade.grade_value = proposal.new_value
    grade.locked_by_proposal_id = None
    grade.updated_at = utcnow()
    proposal.status = result["status"]
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_error(
            "database",
            "grade_update_failed",
            f"correction {proposal_id} executed on-chain but the grade could not be "
            f"updated locally: {exc}",
            502,
            onchain_proposal_id=proposal_id,
            onchain_status=result["status"],
            transaction_hash=result["transaction_hash"],
        )

    proposer = db.session.get(User, proposal.proposer_id)
    return jsonify(
        {
            "status": proposal.status,
            "correction": _correction_dict(proposal, proposer),
            "grade": grade.to_dict(),
            "proposal_id": proposal_id,
            "transaction_hash": result["transaction_hash"],
            "block_number": result["block_number"],
            "signer": result["signer"],
            "role": result["role"],
            "signature_count": result["signature_count"],
            "executed_at": result["executed_at"],
            "record_updated": result["record_updated"],
            "event": result["event"],
        }
    )


@api_v1.post("/grades/corrections/<int:proposal_id>/reject")
@require_auth(roles=["HOD"])
def reject_grade_correction(proposal_id: int):
    """HOD rejects a correction - off-chain only (decision D3).

    Body: ``{"reason": "..."}``. The lock is released and the grade keeps its
    value; the local mirror becomes "Rejected" so later approve attempts get
    409. RecordAuditTrail has no reject, so the on-chain proposal stays
    Pending forever.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_error("request", "missing_fields", "a JSON object is required")
    reason = body.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        return api_error("request", "missing_fields", "reason (non-empty string) is required")

    proposal = _locked_correction(proposal_id)
    if proposal is None:
        db.session.rollback()
        return api_error(
            "proposal", "correction_not_found",
            f"no grade correction with on-chain id {proposal_id}", 404,
        )
    closed = _closed_correction_error(proposal)
    if closed is not None:
        db.session.rollback()
        return closed

    grade = (
        Grade.query.filter_by(
            locked_by_proposal_id=proposal.id, student_id=proposal.student_id,
            course_code=proposal.field[len(GRADE_FIELD_PREFIX):],
        )
        .with_for_update()
        .first()
    )
    if grade is None:
        db.session.rollback()
        return api_error(
            "row_lock", "lock_missing",
            f"no grade row is locked by correction {proposal_id} - it cannot be rejected",
            409,
        )
    grade.locked_by_proposal_id = None
    proposal.status = STATUS_REJECTED
    proposal.rejection_reason = reason
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_error(
            "database", "reject_failed",
            f"correction {proposal_id} could not be rejected: {exc}", 502,
        )

    proposer = db.session.get(User, proposal.proposer_id)
    return jsonify(
        {
            "status": proposal.status,
            "correction": _correction_dict(proposal, proposer),
            "grade": grade.to_dict(),
            "proposal_id": proposal_id,
            "onchain_status": STATUS_PENDING,
        }
    )


@api_v1.get("/grades/corrections")
@require_auth(roles=["FACULTY", "HOD", "ADMIN"])
def list_grade_corrections():
    """Grade corrections, newest first: FACULTY their own, HOD and ADMIN all."""
    query = (
        db.session.query(Proposal, User)
        .outerjoin(User, User.id == Proposal.proposer_id)
        .filter(
            Proposal.field.startswith(GRADE_FIELD_PREFIX),
            Proposal.proposer_id.isnot(None),
        )
    )
    if g.role == "FACULTY":
        query = query.filter(Proposal.proposer_id == g.user_id)
    rows = query.order_by(Proposal.created_at.desc(), Proposal.id.desc()).all()
    corrections = [_correction_dict(proposal, proposer) for proposal, proposer in rows]
    return jsonify({"corrections": corrections, "count": len(corrections)})


@api_v1.get("/grades/<int:student_id>")
@require_auth(roles=["FACULTY", "HOD", "ADMIN", "STUDENT"])
def student_grades(student_id: int):
    """One student's current grades; a STUDENT may read only their own."""
    if g.role == "STUDENT" and student_id != g.user_id:
        return api_error(
            "student", "not_own_grades", "students may only read their own grades", 403,
        )
    rows = Grade.query.filter_by(student_id=student_id).order_by(Grade.course_code).all()
    return jsonify(
        {
            "studentId": student_id,
            "grades": [row.to_dict() for row in rows],
            "count": len(rows),
        }
    )
