"""Authentication and user profile models (Foundation / Stage 0).

Four tables back the authentication and role-governance module:

- ``InstitutionWhitelist`` - official university directory used to verify HODs
  at registration time.
- ``User``                 - lean core account row holding credentials, role,
  institutional email, and active status.
- ``FacultyProfile``       - role-specific profile for faculty members,
  linking user_id to their institutional employee_id.
- ``StudentProfile``       - role-specific profile for students, linking
  user_id to their roll_number and batch.

Roles mirror the institutional governance hierarchy (VC, HOD, FACULTY, STUDENT).
`to_dict()` emits camelCase keys, following the existing codebase convention.
"""

import enum

from ..extensions import db
from ..util import iso_z, utcnow

ROLE_ADMIN = "ADMIN"
ROLE_HOD = "HOD"
ROLE_FACULTY = "FACULTY"
ROLE_STUDENT = "STUDENT"


class UserRole(str, enum.Enum):
    """Institutional roles for system users."""

    ADMIN = "ADMIN"
    HOD = "HOD"
    FACULTY = "FACULTY"
    STUDENT = "STUDENT"


class InstitutionWhitelist(db.Model):
    """University official directory entry for pre-verifying institutional staff."""

    __tablename__ = "institution_whitelist"

    id = db.Column(db.Integer, primary_key=True)

    # Official institutional employee ID (unique across all whitelist records).
    employee_id = db.Column(db.String(64), nullable=False, unique=True, index=True)

    department = db.Column(db.String(128), nullable=False)

    name = db.Column(db.String(128), nullable=False)

    # Institutional email address (unique across all whitelist records).
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the whitelist entry."""
        return {
            "id": self.id,
            "employeeId": self.employee_id,
            "department": self.department,
            "name": self.name,
            "email": self.email,
        }


class User(db.Model):
    """Core user account for all institutional roles."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    # Role matching institutional governance hierarchy.
    role = db.Column(
        db.Enum(UserRole, name="user_roles"),
        nullable=False,
        index=True,
    )

    # Institutional email (unique across all user accounts).
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)

    # Argon2id password hash.
    password_hash = db.Column(db.String(255), nullable=False)

    full_name = db.Column(db.String(128), nullable=False)

    # Nullable for top-level roles like VC that span multiple departments.
    department = db.Column(db.String(128), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Default True; can be flagged False (e.g. anti-spam account block).
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # 1-to-1 relationships to role-specific profile tables
    faculty_profile = db.relationship(
        "FacultyProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    student_profile = db.relationship(
        "StudentProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """JSON-friendly view of the user - never exposes password_hash."""
        return {
            "id": self.id,
            "role": self.role.value if hasattr(self.role, "value") else str(self.role),
            "email": self.email,
            "fullName": self.full_name,
            "department": self.department,
            "createdAt": iso_z(self.created_at) if self.created_at else None,
            "isActive": self.is_active,
        }


class FacultyProfile(db.Model):
    """Role-specific profile extension for faculty accounts."""

    __tablename__ = "faculty_profiles"

    id = db.Column(db.Integer, primary_key=True)

    # FK to the parent User record (1-to-1).
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True
    )

    # Official employee ID (unique across all faculty profiles).
    employee_id = db.Column(db.String(64), nullable=False, unique=True, index=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the faculty profile."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "employeeId": self.employee_id,
        }


class StudentProfile(db.Model):
    """Role-specific profile extension for student accounts."""

    __tablename__ = "student_profiles"

    id = db.Column(db.Integer, primary_key=True)

    # FK to the parent User record (1-to-1).
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True
    )

    # Institutional roll number (unique across all student profiles).
    roll_number = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # Academic batch/cohort (e.g., "2022-2026" or "Fall 2022").
    batch = db.Column(db.String(32), nullable=False)

    def to_dict(self) -> dict:
        """JSON-friendly view of the student profile."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "rollNumber": self.roll_number,
            "batch": self.batch,
        }
