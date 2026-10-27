"""Timetable validation and the shared session-start schedule gate.

Uploaded weekdays/wall times become one dated occurrence on the next matching
local calendar day (including today). Store UTC-naive datetimes so existing
absolute-time seeds and the workload auto-flag job keep their semantics. A row
is not a weekly recurrence: WorkloadLedger permits one outcome per class ID.
"""

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from ..extensions import db
from ..models import FacultyProfile, ScheduledClass, User, UserRole
from ..util import utcnow

COLUMNS = (
    "course_code", "faculty_employee_id", "room", "day_of_week",
    "start_time", "end_time",
)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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
    No end-time/late-start cutoff is introduced by this lower-bound gate.
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
    return None
