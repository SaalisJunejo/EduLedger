"""Small time helpers.

Database columns store *naive UTC* datetimes (SQLite has no real timezone
support), while JSON responses present them with an explicit `+00:00`
offset so JavaScript clients parse them unambiguously.
"""

import secrets
from datetime import datetime, timezone


def generate_nonce():
    """Generates a random unpredictable nonce string combined with a timestamp."""
    random_str = secrets.token_hex(16)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    return f"{random_str}_{timestamp}"


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches the DB column format)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_z(value: datetime) -> str:
    """Format a naive-UTC datetime as an ISO-8601 string with UTC offset."""
    return value.replace(tzinfo=timezone.utc).isoformat()
