"""Shared JSON error helper for the API blueprints.

Every error response carries the machine `error` code, the `check` (the
validation step / endpoint stage) that failed, and a human-readable
`message`. Verbose on purpose so demo failures are easy to debug - these
messages are for developers, not end users in production.
"""

from flask import jsonify


def api_error(check: str, code: str, message: str, status: int = 400, **details):
    """Build a ``(response, status)`` tuple naming the failed check."""
    return jsonify({"error": code, "check": check, "message": message, **details}), status
