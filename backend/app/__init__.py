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
from .extensions import db

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

    # -- Database (SQLAlchemy) --------------------------------------------
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["DB_URL"]
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # Imported here (not at module level) to avoid circular imports;
    # importing registers the models so `create_all` sees them.
    from . import models  # noqa: F401

    with app.app_context():
        db.create_all()

    # -- REST API -----------------------------------------------------------
    from .api import api_v1

    app.register_blueprint(api_v1)

    # -- Background jobs ----------------------------------------------------
    # Rotates attendance QR nonces for active sessions (see app/scheduler.py).
    from .scheduler import init_scheduler

    init_scheduler(app)

    return app
