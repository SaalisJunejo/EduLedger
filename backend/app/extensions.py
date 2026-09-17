"""Shared Flask extensions.

`db` lives here (not in the app factory) so models and blueprints can import
it without circular imports: `app/__init__.py` calls `db.init_app(app)` while
everyone else just does `from app.extensions import db`.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
