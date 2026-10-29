"""Current grades (Phase 1.4a - post-deadline grade alteration, decision D3).

One row per (student, course) holds the grade in force. A grade is never
edited directly after the deadline: a faculty member proposes a correction
on RecordAuditTrail (POST /api/v1/grades/corrections), which locks the row
by pointing `locked_by_proposal_id` at the local Proposal mirror row. Only
the HOD's approval (on-chain signature 2) writes the new value and clears
the lock; a rejection (off-chain only) clears the lock without changing the
value. While the lock is set no second correction can be proposed.
"""

from ..extensions import db
from ..util import iso_z, utcnow


class Grade(db.Model):
    """The current grade of one student in one course."""

    __tablename__ = "grades"
    __table_args__ = (
        db.UniqueConstraint("student_id", "course_code", name="uq_grades_student_course"),
    )

    id = db.Column(db.Integer, primary_key=True)

    # Student.id == User.id by convention (no FK), like everywhere else.
    student_id = db.Column(db.Integer, nullable=False, index=True)

    course_code = db.Column(db.String(64), nullable=False)

    # Opaque grade text ("A", "B+", "C", ...); the audit trail stores it as is.
    grade_value = db.Column(db.String(16), nullable=False)

    # Local Proposal.id of the pending correction holding the row, else null.
    # Deliberately not a FK: reset_local_state.py truncates the proposals
    # mirror after a contract redeploy.
    locked_by_proposal_id = db.Column(db.Integer, nullable=True, index=True)

    # When grade_value last changed (set explicitly - locking the row for a
    # correction is not a grade change).
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self) -> dict:
        """JSON-friendly view of the grade."""
        return {
            "id": self.id,
            "studentId": self.student_id,
            "courseCode": self.course_code,
            "gradeValue": self.grade_value,
            "locked": self.locked_by_proposal_id is not None,
            "lockedByProposalId": self.locked_by_proposal_id,
            "updatedAt": iso_z(self.updated_at),
        }
