"""Timetable validation and the shared session-start schedule gate.

Uploaded weekdays/wall times become one dated occurrence on the next matching
local calendar day (including today). Store UTC-naive datetimes so existing
absolute-time seeds and the workload auto-flag job keep their semantics. A row
is not a weekly recurrence: WorkloadLedger permits one outcome per class ID.

Phase 2.2 adds the master-data helpers: the fixed period grid (seeded
idempotently at app start) and term activation, which creates one blank
TermOffering per section (D11).
"""

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    AttendanceSession, FacultyProfile, Period, ScheduledClass, Section, Term, TermOffering, User,
    UserRole, WorkloadSession,
)
from ..util import utcnow

COLUMNS = (
    "course_code", "faculty_employee_id", "room", "day_of_week",
    "start_time", "end_time",
)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# The canonical grid (PRD v2.2 section 3): (ordinal, start, end, is_break),
# local wall times in TIMETABLE_TIMEZONE. The break row has no ordinal.
PERIOD_GRID = (
    (1, time(8, 40), time(9, 30), False),
    (2, time(9, 30), time(10, 20), False),
    (3, time(10, 20), time(11, 10), False),
    (None, time(11, 10), time(11, 30), True),
    (4, time(11, 30), time(12, 20), False),
    (5, time(12, 20), time(13, 10), False),
    (6, time(13, 10), time(14, 0), False),
)


def timetable_timezone() -> ZoneInfo:
    """Resolve the configured IANA timezone; tzdata supplies it on Windows."""
    try:
        return ZoneInfo(current_app.config["TIMETABLE_TIMEZONE"])
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("TIMETABLE_TIMEZONE must name an installed IANA timezone") from exc


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _day(value) -> int:
    text = str(value).strip().lower()
    if text in DAYS:
        return DAYS.index(text)
    if text in {str(day) for day in range(7)}:
        return int(text)
    raise ValueError("day_of_week must be Monday-Sunday or 0-6 (Monday=0)")


def _time(value, field: str) -> time:
    if isinstance(value, time) and value.tzinfo is None:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value.strip()):
        try:
            return time.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError(f"{field} must be a valid HH:MM or HH:MM:SS local time")


def _local_datetime(date, clock: time, zone: ZoneInfo) -> datetime:
    """Reject ambiguous/nonexistent DST wall times rather than guessing an offset."""
    naive = datetime.combine(date, clock)
    local = naive.replace(tzinfo=zone)
    if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError("time does not exist in TIMETABLE_TIMEZONE on the selected date")
    if local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset():
        raise ValueError("time is ambiguous in TIMETABLE_TIMEZONE on the selected date")
    return local


def validate_row(row: dict, zone: ZoneInfo, now: datetime) -> tuple[ScheduledClass | None, list[str]]:
    """Validate every independent field and resolve the named faculty user."""
    reasons = []
    text = {}
    for field in ("course_code", "faculty_employee_id", "room"):
        value = row.get(field)
        text[field] = "" if value is None else str(value).strip()
        if not text[field]:
            reasons.append(f"{field} is required")
        elif len(text[field]) > 64:
            reasons.append(f"{field} must be at most 64 characters")

    professor = None
    if text["faculty_employee_id"]:
        profile = FacultyProfile.query.filter_by(employee_id=text["faculty_employee_id"]).first()
        professor = db.session.get(User, profile.user_id) if profile else None
        if professor is None:
            reasons.append(f"unknown faculty_employee_id: {text['faculty_employee_id']}")
        elif professor.role != UserRole.FACULTY:
            reasons.append("faculty_employee_id must belong to a FACULTY user")
        elif not professor.is_active:
            reasons.append("faculty_employee_id belongs to an inactive user")

    day = None
    start = end = None
    try:
        day = _day(row.get("day_of_week"))
    except ValueError as exc:
        reasons.append(str(exc))
    for field in ("start_time", "end_time"):
        try:
            clock = _time(row.get(field), field)
            if field == "start_time":
                start = clock
            else:
                end = clock
        except ValueError as exc:
            reasons.append(str(exc))
    if start is not None and end is not None and end <= start:
        reasons.append("end_time must be later than start_time on the same day")
    if reasons:
        return None, reasons

    today = _utc(now).astimezone(zone).date()
    date = today + timedelta(days=(day - today.weekday()) % 7)
    try:
        start_utc = _local_datetime(date, start, zone).astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = _local_datetime(date, end, zone).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError as exc:
        return None, [str(exc)]
    return ScheduledClass(
        course_code=text["course_code"], professor_id=professor.id, room=text["room"],
        day_of_week=day, start_time=start_utc, end_time=end_utc,
    ), []


def start_gate(scheduled_class: ScheduledClass):
    """Return an API error before any session mutation, or None when allowed.

    Null-weekday legacy rows retain absolute UTC semantics. Uploaded rows have
    already had their local weekday/time resolved to an absolute UTC occurrence.
    D19: a row with an end_time may start until that end_time, then too_late;
    a null end_time keeps the lower bound only. The same gate serves attendance
    start, workload start and substitute redeem.
    """
    from ..api.errors import api_error

    grace = current_app.config["SESSION_START_GRACE_MINUTES"]
    if not isinstance(grace, int) or grace < 0:
        return api_error("schedule", "invalid_configuration", "SESSION_START_GRACE_MINUTES must be nonnegative", 500)
    if scheduled_class.day_of_week is not None:
        try:
            zone = timetable_timezone()
        except ValueError as exc:
            return api_error("schedule", "invalid_configuration", str(exc), 500)
        if _utc(scheduled_class.start_time).astimezone(zone).weekday() != scheduled_class.day_of_week:
            return api_error("schedule", "invalid_schedule", "scheduled weekday does not match the dated local start", 400)
    if _utc(utcnow()) < _utc(scheduled_class.start_time) - timedelta(minutes=grace):
        return api_error("schedule", "too_early", f"session may start only within {grace} minutes before the scheduled start", 403)
    if scheduled_class.end_time is not None and _utc(utcnow()) > _utc(scheduled_class.end_time):
        return api_error("schedule", "too_late", "the scheduled class has already ended; a session can no longer be started", 403)
    return None


def first_session_starts(classes) -> dict[int, datetime]:
    """Earliest real UTC start of any session opened for each class (R4.8.4).

    Both start paths record it already: WorkloadSession.started_at (workload
    start and substitute redeem) and AttendanceSession.start_time (attendance
    start, class_id = str(ScheduledClass.id)). Attendance rows survive cold
    resets, so a row only counts from the class's own start gate onwards -
    a re-issued class id never inherits an old lifecycle's session.
    """
    classes = list(classes)
    if not classes:
        return {}
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    by_id = {scheduled_class.id: scheduled_class for scheduled_class in classes}
    starts: dict[int, datetime] = {}

    def keep(class_id: int, started: datetime | None) -> None:
        if started is not None and (class_id not in starts or started < starts[class_id]):
            starts[class_id] = started

    for class_id, started in db.session.query(
        WorkloadSession.class_id, db.func.min(WorkloadSession.started_at),
    ).filter(WorkloadSession.class_id.in_(list(by_id))).group_by(WorkloadSession.class_id):
        keep(class_id, started)

    names = {str(class_id): class_id for class_id in by_id}
    for name, started in db.session.query(
        AttendanceSession.class_id, AttendanceSession.start_time,
    ).filter(AttendanceSession.class_id.in_(list(names))):
        class_id = names[name]
        if started >= by_id[class_id].start_time - grace:
            keep(class_id, started)
    return starts


def lateness(scheduled_start: datetime, actual_start: datetime) -> tuple[bool, int]:
    """(late, lateMinutes) of an actual start against the scheduled start (D18).

    Both instants are compared in TIMETABLE_TIMEZONE. lateMinutes is the whole
    minutes after the scheduled start (0 for an early or on-time start); late
    means strictly more than LATE_AFTER_MINUTES after it.
    """
    late_after = current_app.config["LATE_AFTER_MINUTES"]
    if not isinstance(late_after, int) or late_after < 0:
        raise ValueError("LATE_AFTER_MINUTES must be nonnegative")
    zone = timetable_timezone()
    delta = _utc(actual_start).astimezone(zone) - _utc(scheduled_start).astimezone(zone)
    minutes = max(0, int(delta.total_seconds() // 60))
    return delta > timedelta(minutes=late_after), minutes


def seed_periods() -> int:
    """Insert the PERIOD_GRID rows that are missing; returns how many were added.

    Idempotent: a teaching period is matched by ordinal and the break by
    is_break, so existing rows (including ADMIN edits) are never touched and a
    second call adds nothing. Commits its own rows. If another process seeds
    concurrently, the unique indexes refuse the duplicates and this call
    rolls back and reports 0 - the other process inserted the same rows.
    """
    existing = Period.query.all()
    ordinals = {period.ordinal for period in existing if not period.is_break}
    has_break = any(period.is_break for period in existing)
    created = 0
    for ordinal, start, end, is_break in PERIOD_GRID:
        if (has_break if is_break else ordinal in ordinals):
            continue
        db.session.add(Period(ordinal=ordinal, start_time=start, end_time=end, is_break=is_break))
        created += 1
    if not created:
        return 0
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Suppress only a completed concurrent seed, not unrelated schema errors.
        rows = Period.query.all()
        complete = {row.ordinal for row in rows if not row.is_break} >= {1, 2, 3, 4, 5, 6}
        if complete and any(row.is_break for row in rows):
            return 0
        raise
    return created


def activate_term(term: Term) -> tuple[int, list[int]]:
    """Make `term` the only active term and give every section an offering.

    Deactivates any other active term, then creates one blank TermOffering
    for each Section that has none in this term (R4.1.4, D11). Idempotent: a
    second call deactivates nothing and creates nothing. Not committed here.
    Returns (offerings created, ids of the terms deactivated).
    """
    # Lock in a stable order so concurrent activations of existing terms serialize.
    terms = Term.query.order_by(Term.id).populate_existing().with_for_update().all()
    deactivated = []
    for row in terms:
        if row.id != term.id and row.is_active:
            row.is_active = False
            deactivated.append(row.id)
    if deactivated:
        # Flush the deactivation first: the single-active index is checked per row.
        db.session.flush()
    term.is_active = True
    covered = db.session.query(TermOffering.section_id).filter(TermOffering.term_id == term.id)
    missing = Section.query.filter(~Section.id.in_(covered)).order_by(Section.id).all()
    for section in missing:
        db.session.add(TermOffering(term_id=term.id, section_id=section.id))
    db.session.flush()
    return len(missing), deactivated
