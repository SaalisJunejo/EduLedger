"""Background jobs for the attendance and workload modules.

APScheduler's `BackgroundScheduler` was picked as the simplest reliable
option for Flask: it runs in a daemon thread inside the same process, needs
no external broker, and is started/stopped with the app. Both jobs below
share this single scheduler instance (started once per process by
`init_scheduler`) - the module never creates a second one.

Jobs:
- `rotate_expired_nonces` (every ATTENDANCE_ROTATION_SECONDS) - rotates the QR
  nonce of every active attendance session whose rotation window expired.
  The job only touches expired nonces, which makes rotation idempotent
  within a window - the QR endpoint can rotate lazily as a fallback (see
  `app/api/sessions.py`) without fighting the scheduler. Faculty workload
  sessions (Module 3) are plain AttendanceSession rows, so they rotate here
  too with zero extra code.
- `flag_unconducted_classes` (every SCHEDULER_INTERVAL_SECONDS, default 60) -
  the Module 3 auto-flag: a
  ScheduledClass with a null end_time whose startTime +
  UNCONDUCTED_THRESHOLD_MINUTES (default 15) has passed with no SessionLog
  yet, or one with an end_time that has passed with no SessionLog and no
  session ever started (D19), is anchored on-chain as UNCONDUCTED via
  WorkloadLedger.logWorkload(). In the same sweep, a class with an end_time
  that has passed, a started session and no SessionLog is resolved from its
  attendance evidence (Phase 2.1e): CONDUCTED with check-ins,
  CONDUCTED_ZERO_STUDENTS without, so a session nobody ended still resolves.
  Any session still live on such a class is closed first, in the same
  transaction (Phase 2.1f), so no scan can land after the outcome.

With SCHEDULER_ENABLED false no job is scheduled at all; tests call the job
functions directly instead.
"""

import atexit
import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from .extensions import db
from .models import AttendanceSession, ScheduledClass, SessionLog, WorkloadSession
from .models.workload import STATUS_UNCONDUCTED
from .services import workload
from .services.timetable import first_session_starts
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
            ttl_seconds = app.config["ATTENDANCE_ROTATION_SECONDS"]
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


def overdue_classes(app, now) -> list[ScheduledClass]:
    """The classes the auto-flag would anchor as UNCONDUCTED at `now`.

    Null end_time: unchanged threshold rule - startTime lies more than
    UNCONDUCTED_THRESHOLD_MINUTES before `now` and there is no SessionLog.
    Non-null end_time (D19): end_time has passed, there is no SessionLog
    and no session was ever started for the class, so a late start is never
    pre-empted by a permanent UNCONDUCTED entry. Read-only; needs an app
    context.
    """
    threshold = timedelta(minutes=app.config["UNCONDUCTED_THRESHOLD_MINUTES"])
    legacy = ScheduledClass.query.filter(
        ScheduledClass.end_time.is_(None),
        ScheduledClass.start_time <= now - threshold,
        ~ScheduledClass.session_logs.any(),
    ).all()
    ended = ScheduledClass.query.filter(
        ScheduledClass.end_time.isnot(None),
        ScheduledClass.end_time < now,
        ~ScheduledClass.session_logs.any(),
    ).all()
    started = first_session_starts(ended)
    return legacy + [scheduled_class for scheduled_class in ended if scheduled_class.id not in started]


def unresolved_started_classes(app, now) -> list[ScheduledClass]:
    """Classes whose end_time passed with a session started but no SessionLog.

    The complement of the D19 half of `overdue_classes`: these are never
    flagged UNCONDUCTED; the sweep resolves them from attendance evidence
    instead (Phase 2.1e). Read-only; needs an app context.
    """
    ended = ScheduledClass.query.filter(
        ScheduledClass.end_time.isnot(None),
        ScheduledClass.end_time < now,
        ~ScheduledClass.session_logs.any(),
    ).all()
    started = first_session_starts(ended)
    return [scheduled_class for scheduled_class in ended if scheduled_class.id in started]


def close_live_sessions(scheduled_class: ScheduledClass, now) -> int:
    """End every still-active session of a class whose end_time has passed.

    Attendance sessions get is_active=false and ended_at (so /attendance/scan
    refuses them as session_ended); their WorkloadSession rows, which have no
    ended_at column, get is_active=false. Not committed here - the caller
    commits it together with the outcome. Returns the rows closed.
    """
    session_ids = workload.class_session_ids(scheduled_class)
    if not session_ids:
        return 0
    live = AttendanceSession.query.filter(
        AttendanceSession.id.in_(session_ids),
        AttendanceSession.is_active.is_(True),
    ).all()
    for session in live:
        session.end(now=now)
    closed_workload = WorkloadSession.query.filter(
        WorkloadSession.class_id == scheduled_class.id,
        WorkloadSession.is_active.is_(True),
    ).update({WorkloadSession.is_active: False}, synchronize_session=False)
    return len(live) + closed_workload


def flag_unconducted_classes(app) -> int:
    """Auto-flag overdue un-conducted classes on-chain (Module 3).

    A class is overdue as decided by `overdue_classes`: with a null
    end_time, when its startTime lies more than UNCONDUCTED_THRESHOLD_MINUTES
    in the past (read from `app.config` at call time, so tests can override
    it in-process) and it still has no SessionLog - meaning no student
    scanned, nobody declared zero attendance, and no substitute redeemed a
    session; with an end_time, only once that end_time has passed with no
    session at all. Each overdue class is
    anchored on-chain as UNCONDUCTED (credited to the scheduled professor)
    and a local SessionLog row records the transaction hash.

    The on-chain transaction is sent before the local row is written (the
    chain is the source of truth, the same order as the records module). A
    class whose transaction fails (chain down, or already logged on-chain
    by a race) is skipped with a warning and retried on the next tick if
    applicable; one bad class never blocks the others.

    Then every class from `unresolved_started_classes` has its still-live
    sessions closed (`close_live_sessions`) and goes through
    workload.resolve_class_outcome (the same entry point as
    POST /session/<id>/end): CONDUCTED when check-ins exist,
    CONDUCTED_ZERO_STUDENTS otherwise. Closing and outcome commit together;
    a failed chain write is skipped the same way (the closure is still
    committed, and the outcome is retried on the next tick).

    Returns the number of classes flagged UNCONDUCTED (0 when there is
    nothing to do); resolved classes are logged, not counted.
    """
    with app.app_context():
        try:
            now = utcnow()
            overdue = overdue_classes(app, now)

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
                        professor_id=scheduled_class.professor_id,
                        conducted_by=scheduled_class.professor_id,
                        timestamp=now,
                        onchain_tx_hash=result["transaction_hash"],
                    )
                )
                flagged += 1

            resolved = 0
            closed = 0
            for scheduled_class in unresolved_started_classes(app, now):
                closed += close_live_sessions(scheduled_class, now)
                try:
                    if workload.resolve_class_outcome(scheduled_class, require_closed=False) is not None:
                        resolved += 1
                except workload.WorkloadError as exc:
                    logger.warning(
                        "Outcome resolve skipped class %s: %s", scheduled_class.id, exc.message
                    )

            if flagged or resolved or closed:
                db.session.commit()
            if flagged:
                logger.info("Auto-flagged %d un-conducted class(es) on-chain", flagged)
            if closed:
                logger.info("Closed %d live session row(s) past their class end_time", closed)
            if resolved:
                logger.info("Resolved %d started class(es) from attendance evidence", resolved)
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
        seconds=app.config["ATTENDANCE_ROTATION_SECONDS"],
        args=[app],
        id=ROTATION_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=2,
    )
    scheduler.add_job(
        flag_unconducted_classes,
        trigger="interval",
        seconds=app.config["SCHEDULER_INTERVAL_SECONDS"],  # default 60, per the Module 3 spec
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
        "un-conducted auto-flag (every %ss, threshold %s min)",
        app.config["ATTENDANCE_ROTATION_SECONDS"],
        app.config["SCHEDULER_INTERVAL_SECONDS"],
        app.config["UNCONDUCTED_THRESHOLD_MINUTES"],
    )
    return scheduler
