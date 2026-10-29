"""Record-change proposal model (Module 2 - Multi-Sig Anti-Tamper Audit Trail).

A local mirror of a proposal on the RecordAuditTrail contract. The chain
remains the source of truth (`app/services/audit_trail.py` reads state
straight from the contract); this table exists so the HOD / Exam Controller
dashboard can list pending proposals without scanning chain history. Rows
are created by POST /api/v1/records/propose and their status is synced by
POST /api/v1/records/<id>/approve. Grade corrections (Phase 1.4a,
app/api/grades.py) use the same table with field "grade:<course_code>" and
additionally record the proposing user, the justification text and - for
the off-chain reject - the rejection reason.

Columns follow the codebase snake_case convention; `to_dict()` emits the
camelCase names of the on-chain Proposal struct (studentId, oldValue, ...)
so the local mirror and the chain view read identically in API responses.
"""

from ..extensions import db
from ..util import iso_z, utcnow

# Proposal lifecycle states - the names mirror the contract's ProposalStatus
# enum verbatim, so local rows compare equal with on-chain state.
STATUS_PENDING = "Pending"
STATUS_EXECUTED = "Executed"
# Local-only state (decision D3): an HOD rejected a grade correction off-chain.
# The contract has no reject, so the on-chain proposal stays Pending.
STATUS_REJECTED = "Rejected"


class Proposal(db.Model):
    """A record-change proposal mirrored from the RecordAuditTrail contract."""

    __tablename__ = "proposals"

    id = db.Column(db.Integer, primary_key=True)

    # Student whose record changes (uint256 on-chain; intentionally not a FK
    # to the Module 1 students table - proposals may target students that
    # were never enrolled for biometric attendance).
    student_id = db.Column(db.Integer, nullable=False, index=True)

    # Record field being changed, e.g. "grade" or "attendance".
    field = db.Column(db.String(128), nullable=False)

    # Current and proposed values, stored as strings: the contract keeps them
    # opaque, and numeric vs textual is a concern of the record system, not
    # of the audit trail.
    old_value = db.Column(db.String(255), nullable=False, default="")
    new_value = db.Column(db.String(255), nullable=False, default="")

    # CID of the justification evidence, pinned via POST /api/v1/ipfs/upload.
    ipfs_cid = db.Column(db.String(255), nullable=False)

    # 1-based proposal id on RecordAuditTrail - the join key to chain state.
    onchain_proposal_id = db.Column(db.Integer, nullable=False, unique=True, index=True)

    # STATUS_PENDING or STATUS_EXECUTED, synced from the contract, or the
    # local-only STATUS_REJECTED (grade corrections).
    status = db.Column(db.String(16), nullable=False, default=STATUS_PENDING)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Grade corrections only (null for /records proposals): the FACULTY user
    # who proposed, their justification (also pinned on IPFS as ipfs_cid),
    # and the HOD's reason when the correction was rejected.
    proposer_id = db.Column(db.Integer, nullable=True, index=True)
    justification = db.Column(db.Text, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the proposal (camelCase, like the contract)."""
        return {
            "id": self.id,
            "studentId": self.student_id,
            "field": self.field,
            "oldValue": self.old_value,
            "newValue": self.new_value,
            "ipfsCid": self.ipfs_cid,
            "onchainProposalId": self.onchain_proposal_id,
            "status": self.status,
            "createdAt": iso_z(self.created_at),
        }
