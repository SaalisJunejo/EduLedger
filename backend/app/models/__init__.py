"""Database models.

Importing this package registers the models with SQLAlchemy so that
`db.create_all()` (called by the app factory) sees them.
"""

from .auth import (
    FacultyProfile,
    InstitutionWhitelist,
    StudentProfile,
    User,
    UserRole,
)
from .proposal import Proposal
from .session import AttendanceSession, generate_nonce
from .student import Student
from .workload import ScheduledClass, SessionLog, SubstituteToken, WorkloadSession

__all__ = [
    "AttendanceSession",
    "FacultyProfile",
    "InstitutionWhitelist",
    "Proposal",
    "ScheduledClass",
    "SessionLog",
    "Student",
    "StudentProfile",
    "SubstituteToken",
    "User",
    "UserRole",
    "WorkloadSession",
    "generate_nonce",
]
