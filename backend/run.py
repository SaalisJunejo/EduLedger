"""Development entry point: `python run.py`.

Reads FLASK_CONFIG, SECRET_KEY, and PORT from backend/.env (see .env.example).
For production, run behind a WSGI server, e.g.:

    waitress-serve --port 5000 run:app
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # use_reloader=False keeps a single process (and thus a single background
    # nonce-rotation scheduler). The debug reloader would run a second one in
    # the parent process.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), use_reloader=False)
