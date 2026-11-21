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
from .grade import Grade
from .legacy_mapping import LegacyOccurrenceMapping
from .proposal import Proposal
from .session import AttendanceCheckIn, AttendanceSession, generate_nonce
from .student import Student
from .timetable import (
    CalendarClosure, Course, DirectoryEntry, OccurrenceAudit, Period, Room, Section,
    StudentSection, Term, TermOffering, TimetableSlot,
)
from .workload import ScheduledClass, SessionLog, SubstituteToken, WorkloadSession

__all__ = [
    "AttendanceCheckIn",
    "AttendanceSession",
    "CalendarClosure",
    "Course",
    "DirectoryEntry",
    "FacultyProfile",
    "Grade",
    "InstitutionWhitelist",
    "LegacyOccurrenceMapping",
    "OccurrenceAudit",
    "Period",
    "Proposal",
    "Room",
    "ScheduledClass",
    "Section",
    "SessionLog",
    "Student",
    "StudentProfile",
    "StudentSection",
    "SubstituteToken",
    "Term",
    "TermOffering",
    "TimetableSlot",
    "User",
    "UserRole",
    "WorkloadSession",
    "generate_nonce",
]
