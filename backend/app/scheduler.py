"""Background jobs for the attendance and workload modules.

APScheduler's `BackgroundScheduler` was picked as the simplest reliable
option for Flask: it runs in a daemon thread inside the same process, needs
no external broker, and is started/stopped with the app. Both jobs below
share this single scheduler instance (started once per process by
`init_scheduler`) - the module never creates a second one.

Jobs:
- `rotate_expired_nonces` (every NONCE_ROTATION_SECONDS) - rotates the QR
  nonce of every active attendance session whose rotation window expired.
  The job only touches expired nonces, which makes rotation idempotent
  within a window - the QR endpoint can rotate lazily as a fallback (see
  `app/api/sessions.py`) without fighting the scheduler. Faculty workload
  sessions (Module 3) are plain AttendanceSession rows, so they rotate here
  too with zero extra code.
- `flag_unconducted_classes` (every minute) - the Module 3 auto-flag: any
  ScheduledClass whose startTime + UNCONDUCTED_THRESHOLD_MINUTES (default
  15) has passed with no SessionLog yet is anchored on-chain as
  UNCONDUCTED via WorkloadLedger.logWorkload().
"""

import atexit
import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from .extensions import db
from .models import AttendanceSession, ScheduledClass, SessionLog
from .models.workload import STATUS_UNCONDUCTED
from .services import workload
from .util import utcnow

logger = logging.getLogger(__name__)

ROTATION_JOB_ID = "rotate_attendance_nonces"
AUTOFLAG_JOB_ID = "flag_unconducted_classes"


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


def flag_unconducted_classes(app) -> int:
    """Auto-flag overdue un-conducted classes on-chain (Module 3).

    A class is overdue when its startTime lies more than
    UNCONDUCTED_THRESHOLD_MINUTES in the past (read from `app.config` at
    call time, so tests can override it in-process) and it still has no
    SessionLog - meaning no student scanned, nobody declared zero
    attendance, and no substitute redeemed a session. Each overdue class is
    anchored on-chain as UNCONDUCTED (credited to the scheduled professor)
    and a local SessionLog row records the transaction hash.

    The on-chain transaction is sent before the local row is written (the
    chain is the source of truth, the same order as the records module). A
    class whose transaction fails (chain down, or already logged on-chain
    by a race) is skipped with a warning and retried on the next tick if
    applicable; one bad class never blocks the others.

    Returns the number of classes flagged (0 when there is nothing to do).
    """
    with app.app_context():
        try:
            now = utcnow()
            threshold = timedelta(minutes=app.config["UNCONDUCTED_THRESHOLD_MINUTES"])
            cutoff = now - threshold

            # Classes past the grace window that never produced a SessionLog.
            overdue = (
                ScheduledClass.query.filter(
                    ScheduledClass.start_time <= cutoff,
                    ~ScheduledClass.session_logs.any(),
                )
                .all()
            )

            flagged = 0
            for scheduled_class in overdue:
                try:
                    result = workload.log_workload(
                        scheduled_class.id,
                        scheduled_class.professor_id,
                        STATUS_UNCONDUCTED,
                    )
                except workload.WorkloadError as exc:
                    logger.warning(
                        "Auto-flag skipped class %s: %s", scheduled_class.id, exc.message
                    )
                    continue

                db.session.add(
                    SessionLog(
                        class_id=scheduled_class.id,
                        status=STATUS_UNCONDUCTED,
                        conducted_by=scheduled_class.professor_id,
                        timestamp=now,
                        onchain_tx_hash=result["transaction_hash"],
                    )
                )
                flagged += 1

            if flagged:
                db.session.commit()
                logger.info("Auto-flagged %d un-conducted class(es) on-chain", flagged)
            return flagged
        except Exception:
            db.session.rollback()
            logger.exception("Un-conducted auto-flag job failed")
            return 0


def init_scheduler(app) -> BackgroundScheduler | None:
    """Start the background jobs once per process; returns the scheduler.

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
    scheduler.add_job(
        flag_unconducted_classes,
        trigger="interval",
        seconds=60,  # every minute, per the Module 3 spec
        args=[app],
        id=AUTOFLAG_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    app.extensions["scheduler"] = scheduler
    logger.info(
        "Background jobs started: attendance nonce rotation (every %ss), "
        "un-conducted auto-flag (every 60s, threshold %s min)",
        app.config["NONCE_ROTATION_SECONDS"],
        app.config["UNCONDUCTED_THRESHOLD_MINUTES"],
    )
    return scheduler
