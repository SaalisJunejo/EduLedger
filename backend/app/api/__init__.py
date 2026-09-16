"""Versioned REST API blueprints.

The `api_v1` blueprint is mounted at /api/v1. Feature modules (attendance,
audit trail, faculty workload ledger) will be added as sibling blueprints
under this package, following the same pattern as `health`.
"""

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

from . import health  # noqa: E402,F401  (importing registers the routes)
