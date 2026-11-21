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
from .extensions import db, socketio

__version__ = "0.1.0"


def create_app(config_name: str | None = None, *, initialise_database: bool = True) -> Flask:
    """Build a fully configured Flask application instance.

    ``initialise_database=False`` skips ``create_all`` and ``seed_periods`` so a
    strictly read-only tool (the Phase 2.9 mapping inventory) cannot create
    tables or insert rows. Such a caller must run against a database that
    already has the schema and the Phase 2.9 migration.
    """
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
        if initialise_database:
            db.create_all()
            # Phase 2.2 canonical grid: insert missing periods only (idempotent).
            from .services.timetable import seed_periods

            seed_periods()

    # -- REST API -----------------------------------------------------------
    from .api import api_v1

    app.register_blueprint(api_v1)

    # -- WebSocket (live attendance, Phase 1.2a) ----------------------------
    # The Redis message queue lets several server processes share the
    # session rooms; with REDIS_URL empty, events stay process-local.
    socketio.init_app(
        app,
        message_queue=app.config.get("REDIS_URL") or None,
        cors_allowed_origins=app.config["CORS_ORIGINS"],
    )

    # Imported for its side effect: the @socketio.on handlers register here.
    from . import sockets  # noqa: F401

    # -- Background jobs ----------------------------------------------------
    # Rotates attendance QR nonces for active sessions (see app/scheduler.py).
    from .scheduler import init_scheduler

    init_scheduler(app)

    return app
