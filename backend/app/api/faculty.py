"""Faculty workload endpoints (Module 3 - Faculty Workload Ledger).

- POST /api/v1/faculty/session/start          - professor opens a live
  session for one of their scheduled classes (rotating QR)
- POST /api/v1/faculty/session/scan           - any student scan of the
  live QR marks the class CONDUCTED on-chain (idempotent per class)
- POST /api/v1/faculty/session/zero-attendance - professor declares the
  class was held with zero students (CONDUCTED_ZERO_STUDENTS on-chain)
- POST /api/v1/faculty/substitute/issue       - professor mints a
  short-lived signed token authorizing a named substitute for one class
- POST /api/v1/faculty/substitute/redeem      - substitute redeems the
  token and starts the session (workload credits the substitute)
- GET  /api/v1/faculty/logs                   - every SessionLog entry
  (admin / history view)

The live QR deliberately reuses the Module 1 attendance session machinery
end to end instead of duplicating it: sessions opened here are plain
``AttendanceSession`` rows (class_id ``workload-<id>``), so the existing
background nonce-rotation job keeps their QRs rotating every
NONCE_ROTATION_SECONDS and the existing GET /api/v1/session/<id>/qr
projector endpoint serves them unchanged. Only the conductor bookkeeping
(``WorkloadSession``) and the on-chain workload anchoring are new.

Every failure response carries {error, check, message} naming the failed
stage, exactly like the Module 1/2 endpoints: "request" (input validation),
"class" (unknown class), "professor" (ownership), "session_nonce" (QR
session / nonce gates, same names as /attendance/scan), "session"
(zero-attendance session gate), "session_log" (class already concluded),
"substitute_token" (token validity), "substitute" (substitute identity),
"workload_ledger" (chain unreachable or unconfigured).
"""

from datetime import datetime, timezone

from flask import current_app, jsonify, request

from ..extensions import db
from ..models import (
    AttendanceSession,
    ScheduledClass,
    SessionLog,
    SubstituteToken,
    WorkloadSession,
)
from ..models.workload import STATUS_CONDUCTED, STATUS_CONDUCTED_ZERO_STUDENTS
from ..services import workload
from ..services.substitute_token import (
    SubstituteTokenError,
    mint as mint_substitute_token,
    verify as verify_substitute_token,
)
from ..util import iso_z, utcnow
from . import api_v1
from .errors import api_error

# WorkloadError code -> (check, HTTP status) for the shared error shape.
_ERROR_RESPONSES = {
    "workload_already_logged": ("session_log", 409),
    "invalid_status": ("request", 400),
}


def _workload_error(exc: workload.WorkloadError):
    """Map a WorkloadError to the shared {error, check, message} shape."""
    check, status = _ERROR_RESPONSES.get(exc.code, ("workload_ledger", 502))
    return api_error(check, exc.code, exc.message, status)


def _attendance_class_id(scheduled_class_id: int) -> str:
    """Namespace faculty QR sessions inside the Module 1 session table.

    Keeps workload sessions and plain attendance sessions (whose class_id
    is a human code like "BSCS-401") in one table without ever colliding.
    """
    return f"workload-{scheduled_class_id}"


def _start_live_session(scheduled_class: ScheduledClass, conductor_id: int):
    """Open the rotating-QR session for a class; returns (session, workload).

    Reuses the Module 1 AttendanceSession machinery: the row created here
    is rotated by the existing background job and its QR is served by the
    existing GET /api/v1/session/<id>/qr endpoint, so no nonce logic is
    duplicated in this module. Deactivates any previous live session of
    the class first (one live session per class, the same rule as
    app/api/sessions.py). Callers commit.
    """
    now = utcnow()
    ttl_seconds = current_app.config["NONCE_ROTATION_SECONDS"]
    attendance_class_id = _attendance_class_id(scheduled_class.id)

    for previous in AttendanceSession.query.filter_by(
        class_id=attendance_class_id, is_active=True
    ).all():
        previous.is_active = False
    for previous in WorkloadSession.query.filter_by(
        class_id=scheduled_class.id, is_active=True
    ).all():
        previous.is_active = False

    attendance_session = AttendanceSession(class_id=attendance_class_id)
    attendance_session.rotate_nonce(now=now, ttl_seconds=ttl_seconds)
    db.session.add(attendance_session)
    db.session.flush()  # the workload row links to attendance_session.id

    workload_session = WorkloadSession(
        class_id=scheduled_class.id,
        attendance_session_id=attendance_session.id,
        conductor_id=conductor_id,
        started_at=now,
        is_active=True,
    )
    db.session.add(workload_session)
    return attendance_session, workload_session


def _anchor_session_log(scheduled_class: ScheduledClass, conductor_id: int, status: str) -> tuple[SessionLog, dict]:
    """Log the class outcome on-chain first, then persist the local row.

    Same order as the Module 2 propose endpoint: a failed transaction must
    never leave a local row behind. Returns (session_log, chain_result).
    Callers commit.
    """
    result = workload.log_workload(scheduled_class.id, conductor_id, status)
    session_log = SessionLog(
        class_id=scheduled_class.id,
        status=status,
        conducted_by=conductor_id,
        timestamp=utcnow(),
        onchain_tx_hash=result["transaction_hash"],
    )
    db.session.add(session_log)
    return session_log, result


@api_v1.post("/faculty/session/start")
def faculty_start_session():
    """Start the live session for one of the professor's scheduled classes.

    Body: ``{"professor_id": 101, "class_id": 7}``. The rotating QR for the
    classroom projector is served by the existing Module 1 endpoint at
    ``qr_poll_url`` (poll every 1-2 seconds). Only the scheduled professor
    may start the class, and a class whose workload was already logged
    cannot be started again (the contract allows one entry per class).
    Professor authentication is not enforced yet - the body carries the id,
    wired up with the JWT layer later (see roadmap).
    """
    body = request.get_json(silent=True) or {}
    professor_id = body.get("professor_id")
    class_id = body.get("class_id")

    if not isinstance(professor_id, int) or professor_id <= 0:
        return api_error("request", "missing_fields", "professor_id (positive int) is required")
    if not isinstance(class_id, int) or class_id <= 0:
        return api_error("request", "missing_fields", "class_id (positive int) is required")

    scheduled_class = db.session.get(ScheduledClass, class_id)
    if scheduled_class is None:
        return api_error("class", "class_not_found", f"scheduled class {class_id} does not exist", 404)

    if scheduled_class.professor_id != professor_id:
        return api_error(
            "professor",
            "not_class_professor",
            f"class {class_id} is scheduled for professor {scheduled_class.professor_id}, "
            f"not {professor_id}",
            403,
        )

    if SessionLog.query.filter_by(class_id=class_id).first() is not None:
        return api_error(
            "session_log",
            "class_already_logged",
            f"class {class_id} already has a final workload entry on-chain "
            "(one entry per class)",
            409,
        )

    attendance_session, workload_session = _start_live_session(scheduled_class, professor_id)
    db.session.commit()

    return (
        jsonify(
            {
                "session": attendance_session.to_dict(),
                "workload_session": workload_session.to_dict(),
                "scheduled_class": scheduled_class.to_dict(),
                "qr_poll_url": f"/api/v1/session/{attendance_session.id}/qr",
                "rotation_seconds": current_app.config["NONCE_ROTATION_SECONDS"],
            }
        ),
        201,
    )


@api_v1.post("/faculty/session/scan")
def faculty_scan():
    """A student scan of the live QR: the class is CONDUCTED, anchored on-chain.

    Body: ``{"session_id": 12, "nonce": "<scanned from the QR>"}`` (an
    optional ``student_id`` is accepted but not required - any single scan
    is enough to prove the session is live).

    Validation order (each failure response names its check, mirroring
    /attendance/scan): (a) session exists, (b) session still active,
    (c) scanned nonce matches the current rotating nonce, (d) nonce not
    expired, (e) the QR belongs to a faculty workload session. Then, if
    the class has no SessionLog yet, WorkloadLedger.logWorkload() is sent
    from the backend signer with status CONDUCTED, credited to the session's
    conductor. A scan for an already-logged class is an idempotent no-op
    (200) - only the first scan needs to succeed.
    """
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    nonce = body.get("nonce")

    missing = [
        field
        for field, value in (("session_id", session_id), ("nonce", nonce))
        if value in (None, "")
    ]
    if missing:
        return api_error(
            "request", "missing_fields", f"missing required field(s): {', '.join(missing)}"
        )

    # -- (a)-(d) session + rotating nonce (same gates as /attendance/scan) --
    session = db.session.get(AttendanceSession, session_id) if isinstance(session_id, int) else None
    if session is None:
        return api_error(
            "session_nonce", "session_not_found", f"session {session_id} does not exist", 404
        )
    if not session.is_active:
        return api_error(
            "session_nonce",
            "session_inactive",
            f"session {session_id} is no longer active",
            409,
        )
    if str(nonce) != session.current_nonce:
        return api_error(
            "session_nonce",
            "nonce_mismatch",
            "scanned nonce does not match the session's current nonce "
            "(the QR may have rotated - rescan)",
        )
    if session.nonce_is_expired():
        return api_error(
            "session_nonce",
            "nonce_expired",
            "the session's current nonce has expired (QR rotation lagged)",
        )

    # -- (e) the QR must belong to a faculty workload session ---------------
    workload_session = WorkloadSession.query.filter_by(
        attendance_session_id=session.id
    ).first()
    if workload_session is None:
        return api_error(
            "workload_session",
            "not_a_workload_session",
            f"session {session_id} was not started via the faculty workload module",
            409,
        )

    scheduled_class = db.session.get(ScheduledClass, workload_session.class_id)
    if scheduled_class is None:
        return api_error(
            "class", "class_not_found", f"scheduled class {workload_session.class_id} does not exist", 404
        )

    # Idempotent per class: only the first successful scan anchors CONDUCTED.
    existing = SessionLog.query.filter_by(class_id=scheduled_class.id).first()
    if existing is not None:
        return jsonify({"status": "already_logged", "session_log": existing.to_dict()})

    try:
        session_log, result = _anchor_session_log(
            scheduled_class, workload_session.conductor_id, STATUS_CONDUCTED
        )
    except workload.WorkloadError as exc:
        return _workload_error(exc)
    db.session.commit()

    return (
        jsonify(
            {
                "status": "conducted",
                "session_log": session_log.to_dict(),
                "transaction_hash": result["transaction_hash"],
                "block_number": result["block_number"],
                "logged_at": result["logged_at"],
                "signer": result["signer"],
            }
        ),
        201,
    )


@api_v1.post("/faculty/session/zero-attendance")
def zero_attendance():
    """Professor declares the class was held with zero students.

    Body: ``{"professor_id": 101, "class_id": 7}``. Only allowed when a
    session was properly started for the class (the professor - or a
    substitute covering it - is its live conductor); the class is then
    anchored on-chain as CONDUCTED_ZERO_STUDENTS, still crediting the
    conductor's workload.
    """
    body = request.get_json(silent=True) or {}
    professor_id = body.get("professor_id")
    class_id = body.get("class_id")

    if not isinstance(professor_id, int) or professor_id <= 0:
        return api_error("request", "missing_fields", "professor_id (positive int) is required")
    if not isinstance(class_id, int) or class_id <= 0:
        return api_error("request", "missing_fields", "class_id (positive int) is required")

    scheduled_class = db.session.get(ScheduledClass, class_id)
    if scheduled_class is None:
        return api_error("class", "class_not_found", f"scheduled class {class_id} does not exist", 404)

    workload_session = WorkloadSession.query.filter_by(class_id=class_id, is_active=True).first()
    if workload_session is None:
        return api_error(
            "session",
            "session_not_started",
            f"no live session for class {class_id} - start the session first "
            "(zero attendance can only be declared for a started class)",
            409,
        )

    if workload_session.conductor_id != professor_id:
        return api_error(
            "professor",
            "not_session_conductor",
            f"class {class_id} is currently conducted by professor "
            f"{workload_session.conductor_id}, not {professor_id}",
            403,
        )

    attendance_session = db.session.get(AttendanceSession, workload_session.attendance_session_id)
    if attendance_session is None or not attendance_session.is_active:
        return api_error(
            "session",
            "session_inactive",
            f"the live session for class {class_id} is no longer active - "
            "start a new session first",
            409,
        )

    if SessionLog.query.filter_by(class_id=class_id).first() is not None:
        return api_error(
            "session_log",
            "workload_already_logged",
            f"class {class_id} already has a final workload entry on-chain",
            409,
        )

    try:
        session_log, result = _anchor_session_log(
            scheduled_class, workload_session.conductor_id, STATUS_CONDUCTED_ZERO_STUDENTS
        )
    except workload.WorkloadError as exc:
        return _workload_error(exc)
    db.session.commit()

    return (
        jsonify(
            {
                "status": "conducted_zero_students",
                "session_log": session_log.to_dict(),
                "transaction_hash": result["transaction_hash"],
                "block_number": result["block_number"],
                "logged_at": result["logged_at"],
                "signer": result["signer"],
            }
        ),
        201,
    )


@api_v1.post("/faculty/substitute/issue")
def issue_substitute():
    """Mint a short-lived signed token authorizing a substitute for one class.

    Body: ``{"professor_id": 101, "class_id": 7, "substitute_id": 102}``.
    The token is an HS256 JWT valid for SUBSTITUTE_TOKEN_MINUTES
    (default 30 minutes) that only the named substitute can redeem, for
    this one class only. The SubstituteToken row tracks its redemption.
    """
    body = request.get_json(silent=True) or {}
    professor_id = body.get("professor_id")
    class_id = body.get("class_id")
    substitute_id = body.get("substitute_id")

    if not isinstance(professor_id, int) or professor_id <= 0:
        return api_error("request", "missing_fields", "professor_id (positive int) is required")
    if not isinstance(class_id, int) or class_id <= 0:
        return api_error("request", "missing_fields", "class_id (positive int) is required")
    if not isinstance(substitute_id, int) or substitute_id <= 0:
        return api_error("request", "missing_fields", "substitute_id (positive int) is required")

    scheduled_class = db.session.get(ScheduledClass, class_id)
    if scheduled_class is None:
        return api_error("class", "class_not_found", f"scheduled class {class_id} does not exist", 404)

    if scheduled_class.professor_id != professor_id:
        return api_error(
            "professor",
            "not_class_professor",
            f"class {class_id} is scheduled for professor {scheduled_class.professor_id}, "
            f"not {professor_id}",
            403,
        )

    if substitute_id == professor_id:
        return api_error(
            "request",
            "invalid_substitute",
            "substitute_id must differ from the issuing professor",
        )

    token, expires_at_unix = mint_substitute_token(professor_id, substitute_id, class_id)
    # JWT exp is a unix timestamp; store it as the same naive-UTC datetime
    # format every other model column uses.
    expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc).replace(tzinfo=None)

    record = SubstituteToken(
        issued_by=professor_id,
        class_id=class_id,
        token=token,
        expires_at=expires_at,
    )
    db.session.add(record)
    db.session.commit()

    return (
        jsonify(
            {
                "substitute_token": token,
                "substitute_id": substitute_id,
                "class_id": class_id,
                "issued_by": professor_id,
                "expires_at": iso_z(expires_at),
                "expires_in_minutes": int(current_app.config["SUBSTITUTE_TOKEN_MINUTES"]),
            }
        ),
        201,
    )


@api_v1.post("/faculty/substitute/redeem")
def redeem_substitute():
    """Redeem a substitute token and start the class session as the substitute.

    Body: ``{"token": "<jwt>", "substitute_id": 102}``. The token must be
    valid, unexpired, issued for this substitute, and not redeemed before;
    the class must not be concluded yet. On success the live session starts
    with the substitute as conductor - so the workload (SessionLog's
    conductedBy) is later credited to the substitute, not the scheduled
    professor.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    substitute_id = body.get("substitute_id")

    if not token or not isinstance(substitute_id, int) or substitute_id <= 0:
        return api_error(
            "request", "missing_fields", "token (string) and substitute_id (positive int) are required"
        )

    try:
        claims = verify_substitute_token(str(token))
    except SubstituteTokenError as exc:
        return api_error("substitute_token", exc.code, exc.message, 401)

    if claims.get("sub") != str(substitute_id):
        return api_error(
            "substitute",
            "substitute_mismatch",
            f"this token authorizes substitute {claims.get('sub')}, not {substitute_id}",
            403,
        )

    record = SubstituteToken.query.filter_by(token=str(token)).first()
    if record is None:
        return api_error(
            "substitute_token",
            "token_not_found",
            "this token was not issued by the API (only tokens from "
            "/faculty/substitute/issue can be redeemed)",
            404,
        )
    if record.redeemed_at is not None:
        return api_error(
            "substitute_token",
            "token_already_redeemed",
            f"this token was already redeemed by professor {record.redeemed_by}",
            409,
        )

    scheduled_class = db.session.get(ScheduledClass, record.class_id)
    if scheduled_class is None:
        return api_error(
            "class", "class_not_found", f"scheduled class {record.class_id} does not exist", 404
        )

    if SessionLog.query.filter_by(class_id=scheduled_class.id).first() is not None:
        return api_error(
            "session_log",
            "class_already_logged",
            f"class {scheduled_class.id} already has a final workload entry on-chain",
            409,
        )

    # Redemption is one-shot: mark the token, then start the live session
    # with the substitute as conductor (a single commit covers both).
    record.redeemed_by = substitute_id
    record.redeemed_at = utcnow()
    attendance_session, workload_session = _start_live_session(scheduled_class, substitute_id)
    db.session.commit()

    return (
        jsonify(
            {
                "status": "redeemed",
                "substitute_id": substitute_id,
                "class_id": scheduled_class.id,
                "session": attendance_session.to_dict(),
                "workload_session": workload_session.to_dict(),
                "qr_poll_url": f"/api/v1/session/{attendance_session.id}/qr",
                "rotation_seconds": current_app.config["NONCE_ROTATION_SECONDS"],
            }
        ),
        201,
    )


@api_v1.get("/faculty/logs")
def list_logs():
    """Every SessionLog entry, newest first, for the admin / history view.

    Each entry carries the anchored outcome, the credited conductor, the
    on-chain transaction hash and the scheduled class it belongs to.
    """
    logs = SessionLog.query.order_by(SessionLog.timestamp.desc()).all()
    entries = []
    for log in logs:
        entry = log.to_dict()
        entry["class"] = log.scheduled_class.to_dict() if log.scheduled_class else None
        entries.append(entry)
    return jsonify({"logs": entries, "count": len(entries)})
