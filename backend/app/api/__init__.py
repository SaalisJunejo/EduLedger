"""Versioned REST API blueprints.

The `api_v1` blueprint is mounted at /api/v1. Feature modules (attendance
sessions, identity verification, audit trail, faculty workload ledger) are
added as sibling modules under this package, following the same pattern as
`health`.
"""

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

from . import admin, attendance, faculty, health, ipfs, records, sessions, students  # noqa: E402,F401  (importing registers the routes)
