"""Small time helpers.

Database columns store *naive UTC* datetimes (SQLite has no real timezone
support), while JSON responses present them with an explicit `+00:00`
offset so JavaScript clients parse them unambiguously.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches the DB column format)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_z(value: datetime) -> str:
    """Format a naive-UTC datetime as an ISO-8601 string with UTC offset."""
    return value.replace(tzinfo=timezone.utc).isoformat()
