"""Development entry point: `python run.py`.

Reads FLASK_CONFIG, SECRET_KEY, and PORT from backend/.env (see .env.example).
Served through `socketio.run` (Phase 1.2a) so the live attendance WebSocket
shares the port with the REST API. For production, run behind a WSGI server
with WebSocket support, e.g.:

    gunicorn -k gthread -w 1 run:app
"""

import os

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    # use_reloader=False keeps a single process (and thus a single background
    # nonce-rotation scheduler). The debug reloader would run a second one in
    # the parent process. allow_unsafe_werkzeug lets the Werkzeug dev server
    # carry the WebSocket - fine locally, never in production.
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=app.debug,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
