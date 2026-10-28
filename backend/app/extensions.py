"""Shared Flask extensions.

`db` and `socketio` live here (not in the app factory) so models, blueprints
and the socket handlers can import them without circular imports:
`app/__init__.py` calls `db.init_app(app)` / `socketio.init_app(app, ...)`
while everyone else just does `from app.extensions import db, socketio`.

`async_mode="threading"` is deliberate (decision D5): the WS transport is
served by simple-websocket, so nothing monkey-patches the standard library
and psycopg2, web3 and APScheduler keep working unchanged. The Redis
message queue is attached in the factory, where REDIS_URL is readable.
"""

from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
socketio = SocketIO(async_mode="threading")
