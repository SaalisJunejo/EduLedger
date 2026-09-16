"""EduLedger backend - Flask application factory.

Usage:
    from app import create_app

    app = create_app()             # uses FLASK_CONFIG (default: development)
    app = create_app("testing")    # explicit profile

The factory pattern keeps module blueprints (attendance, audit trail,
faculty workload ledger) independently registerable as they are built.
"""

import os

from flask import Flask
from flask_cors import CORS

from .config import CONFIG_BY_NAME

__version__ = "0.1.0"


def create_app(config_name: str | None = None) -> Flask:
    """Build a fully configured Flask application instance."""
    app = Flask(__name__)

    config_name = config_name or os.getenv("FLASK_CONFIG", "development")
    config_class = CONFIG_BY_NAME.get(config_name)
    if config_class is None:
        raise ValueError(
            f"Unknown config '{config_name}'. Expected one of: {', '.join(sorted(CONFIG_BY_NAME))}."
        )

    app.config.from_object(config_class)
    app.config["ENVIRONMENT"] = config_name

    CORS(app, origins=app.config["CORS_ORIGINS"])

    # Imported here (not at module level) to avoid circular imports.
    from .api import api_v1

    app.register_blueprint(api_v1)

    return app
