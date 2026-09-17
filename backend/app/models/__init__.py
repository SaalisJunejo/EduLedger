"""Database models.

Importing this package registers the models with SQLAlchemy so that
`db.create_all()` (called by the app factory) sees them.
"""

from .session import AttendanceSession, generate_nonce

__all__ = ["AttendanceSession", "generate_nonce"]
