"""Background nonce rotation for active attendance sessions.

APScheduler's `BackgroundScheduler` was picked as the simplest reliable
option for Flask: it runs in a daemon thread inside the same process, needs
no external broker, and is started/stopped with the app.

The job only rotates nonces whose rotation window has already expired, which
makes rotation idempotent within a window - the QR endpoint can rotate lazily
as a fallback (see `app/api/sessions.py`) without fighting the scheduler.
"""

import atexit
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .extensions import db
from .models import AttendanceSession
from .util import utcnow

logger = logging.getLogger(__name__)

ROTATION_JOB_ID = "rotate_attendance_nonces"


def rotate_expired_nonces(app) -> int:
    """Give every active session with an expired nonce a fresh one.

    Runs inside its own app context because Flask-SQLAlchemy requires one,
    and swallows exceptions so a transient DB error never kills the job.

    Returns the number of sessions rotated (0 when there is nothing to do).
    """
    with app.app_context():
        try:
            now = utcnow()
            ttl_seconds = app.config["NONCE_ROTATION_SECONDS"]
            expired = (
                AttendanceSession.query.filter(
                    AttendanceSession.is_active.is_(True),
                    AttendanceSession.nonce_expires_at <= now,
                )
                .all()
            )
            for session in expired:
                session.rotate_nonce(now=now, ttl_seconds=ttl_seconds)
            if expired:
                db.session.commit()
                logger.debug("Rotated nonces for %d session(s)", len(expired))
            return len(expired)
        except Exception:
            db.session.rollback()
            logger.exception("Nonce rotation job failed")
            return 0


def init_scheduler(app) -> BackgroundScheduler | None:
    """Start the rotation job once per process; returns the scheduler.

    No-op when `SCHEDULER_ENABLED` is false (tests, one-off scripts) or when
    this app instance already has a scheduler (e.g. the factory ran twice).
    """
    if not app.config.get("SCHEDULER_ENABLED"):
        return None
    if "scheduler" in app.extensions:
        return app.extensions["scheduler"]

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        rotate_expired_nonces,
        trigger="interval",
        seconds=app.config["NONCE_ROTATION_SECONDS"],
        args=[app],
        id=ROTATION_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=2,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    app.extensions["scheduler"] = scheduler
    logger.info(
        "Attendance nonce rotation started (every %ss)", app.config["NONCE_ROTATION_SECONDS"]
    )
    return scheduler
