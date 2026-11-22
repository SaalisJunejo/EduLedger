"""Future-only materialisation and audited, off-chain occurrence exceptions.

Callers own the transaction. PostgreSQL writers share an advisory transaction
lock, then lock affected rows; the common start gate takes the same row lock.
No method here calls the chain or changes the weekly pattern.
"""

from datetime import date, datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import text

from ..extensions import db
from ..models import (
    AttendanceSession, CalendarClosure, Course, OccurrenceAudit, Period, Room,
    ScheduledClass, SessionLog, SubstituteToken, Term, TermOffering, TimetableSlot,
    User, UserRole, WorkloadSession,
)
from ..models.workload import STATUS_CONDUCTED, STATUS_CONDUCTED_ZERO_STUDENTS, STATUS_UNCONDUCTED
from ..util import utcnow
from .timetable import _local_datetime, _utc, first_session_starts, lateness, timetable_timezone


class OccurrenceError(Exception):
    def __init__(self, error, message, status=400):
        super().__init__(message)
        self.error, self.message, self.status = error, message, status


def lock_writes():
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(250025)"))


def local_date(instant):
    return _utc(instant).astimezone(timetable_timezone()).date()


def today():
    return local_date(utcnow())


def utc_bounds(day_from, day_to):
    zone = timetable_timezone()
    start = datetime.combine(day_from, datetime.min.time(), zone)
    end = datetime.combine(day_to + timedelta(days=1), datetime.min.time(), zone)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


def period_times(day, period):
    if period.is_break or period.ordinal not in range(1, 7):
        raise OccurrenceError("break_period", "an occurrence must use a teaching period, never the break")
    zone = timetable_timezone()
    return tuple(_local_datetime(day, clock, zone).astimezone(timezone.utc).replace(tzinfo=None)
                 for clock in (period.start_time, period.end_time))


def cancelled_ids():
    return db.session.query(OccurrenceAudit.occurrence_id).filter(
        OccurrenceAudit.action == "CANCEL", OccurrenceAudit.occurrence_id.isnot(None),
    )


def is_cancelled(row):
    return OccurrenceAudit.query.filter_by(action="CANCEL", occurrence_id=row.id).first() is not None


def department_of(row):
    if row.slot is not None:
        return row.slot.offering.section.department
    professor = db.session.get(User, row.professor_id)
    return professor.department if professor else None


def has_session(row):
    """Any session, including ended sessions and orphaned workload-name rows.

    Deliberately no grace filter: destructive operations must preserve evidence.
    """
    return (WorkloadSession.query.filter_by(class_id=row.id).first() is not None or
            AttendanceSession.query.filter(AttendanceSession.class_id.in_(
                [str(row.id), f"workload-{row.id}"])).first() is not None)


def matching_closures(day, department, closures=None):
    closures = CalendarClosure.query.all() if closures is None else closures
    return [c for c in closures if c.date_from <= day <= c.date_to and
            (c.scope == "UNIVERSITY" or c.department == department)]


def exception_keys():
    """Original dates stay consumed even when an occurrence moves or is deleted."""
    return {(a.slot_id, a.date_from) for a in OccurrenceAudit.query.filter(
        OccurrenceAudit.action.in_(["CANCEL", "MOVE"]), OccurrenceAudit.slot_id.isnot(None),
    ).all()}


def generate_for_term(term_id, window_days=None):
    """Tomorrow through today + window_days, inclusive; never mutate old rows.

    Configuration is read at call time so explicit test overrides are honored.
    Returns counts and leaves commit/rollback to the caller.
    """
    days = current_app.config["TIMETABLE_WINDOW_DAYS"] if window_days is None else window_days
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
        raise OccurrenceError("invalid_fields", "TIMETABLE_WINDOW_DAYS must be between 1 and 366")
    lock_writes()
    term = Term.query.filter_by(id=term_id).populate_existing().with_for_update().first()
    if term is None:
        raise OccurrenceError("term_not_found", "term not found", 404)
    slots = TimetableSlot.query.join(TermOffering).filter(TermOffering.term_id == term.id).all()
    current = today()
    closures = CalendarClosure.query.all()
    existing = {(row.slot_id, local_date(row.start_time)) for row in
                ScheduledClass.query.filter_by(term_id=term.id).all()}
    existing |= exception_keys()
    counts = {"created": 0, "skipped_existing": 0, "skipped_closed": 0}
    for offset in range(1, days + 1):
        day = current + timedelta(days=offset)
        if day.weekday() > 4 or not term.start_date <= day <= term.end_date:
            continue
        for slot in slots:
            if slot.day_of_week != day.weekday():
                continue
            if matching_closures(day, slot.offering.section.department, closures):
                counts["skipped_closed"] += 1
                continue
            key = (slot.id, day)
            if key in existing:
                counts["skipped_existing"] += 1
                continue
            start, end = period_times(day, slot.period)
            db.session.add(ScheduledClass(
                slot_id=slot.id, term_id=term.id, session_group_id=slot.session_group_id,
                professor_id=slot.faculty_id, course_code=slot.course.code, room=slot.room.name,
                day_of_week=slot.day_of_week, start_time=start, end_time=end,
            ))
            existing.add(key)
            counts["created"] += 1
    db.session.flush()
    return counts


def reason_text(value):
    # Whitespace anywhere (per str.isspace) does not count toward the minimum;
    # only the edges are trimmed from the stored text.
    if not isinstance(value, str) or sum(not char.isspace() for char in value) < 3:
        raise OccurrenceError("invalid_reason", "reason must contain at least 3 non-whitespace characters")
    return value.strip()


def closure_reason_text(value):
    # A closure reason is optional; "" records that none was given.
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    return reason_text(value)


def audit(actor_id, action, day_from, day_to, reason, **values):
    entry = OccurrenceAudit(actor_id=actor_id, action=action, date_from=day_from,
                            date_to=day_to, reason=reason, **values)
    db.session.add(entry)
    return entry


def mark_closure(body, actor):
    if actor is None or not actor.is_active or actor.role not in {UserRole.ADMIN, UserRole.HOD}:
        raise OccurrenceError("forbidden", "an active HOD or ADMIN is required", 403)
    reason = closure_reason_text(body.get("reason"))
    try:
        first, last = (date.fromisoformat(body[key]) for key in ("date_from", "date_to"))
    except (KeyError, ValueError, TypeError):
        raise OccurrenceError("invalid_fields", "date_from and date_to must be ISO dates") from None
    if last < first:
        raise OccurrenceError("invalid_fields", "date_to must not precede date_from")
    scope = str(body.get("scope", "DEPARTMENT")).upper()
    department = body.get("department")
    if actor.role == UserRole.HOD:
        if not actor.department or scope != "DEPARTMENT" or department not in (None, actor.department):
            raise OccurrenceError("out_of_department", "HOD may close only their own department", 403)
        department = actor.department
    elif scope == "UNIVERSITY":
        department = None
    if scope not in {"UNIVERSITY", "DEPARTMENT"} or (scope == "DEPARTMENT" and
            (not isinstance(department, str) or not department.strip() or len(department) > 128)):
        raise OccurrenceError("invalid_fields", "scope must be UNIVERSITY or DEPARTMENT with department")
    lock_writes()
    closure = CalendarClosure(date_from=first, date_to=last, scope=scope, department=department,
                              reason=reason, created_by=actor.id)
    db.session.add(closure)
    db.session.flush()
    future = max(first, today() + timedelta(days=1))
    removed = kept = kept_logged = 0
    removed_snapshots = []
    # Imported here: legacy_mapping imports this module at load time.
    from .legacy_mapping import release_for_deleted_occurrence
    if future <= last:
        start, end = utc_bounds(future, last)
        rows = ScheduledClass.query.filter(ScheduledClass.start_time >= start,
                                           ScheduledClass.start_time < end).order_by(
                                               ScheduledClass.id).populate_existing().with_for_update().all()
        for row in rows:
            if scope == "DEPARTMENT" and department_of(row) != department:
                continue
            if has_session(row):
                kept += 1
                continue
            if SessionLog.query.filter_by(class_id=row.id).first() is not None:
                kept_logged += 1  # irreversible history must not be orphaned
                continue
            removed_snapshots.append(dict(row.to_dict(), department=department_of(row)))
            SubstituteToken.query.filter_by(class_id=row.id).delete(synchronize_session=False)
            # D15 still deletes the occurrence; the mapping becomes history.
            release_for_deleted_occurrence(row.id)
            db.session.delete(row)
            removed += 1
    audit(actor.id, "CLOSURE_CREATE", first, last, reason, closure_id=closure.id,
          removed_count=removed, details={"scope": scope, "department": department,
                                        "kept_started": kept, "kept_logged": kept_logged,
                                        "removed_occurrences": removed_snapshots})
    db.session.flush()
    return closure, {"removed": removed, "kept_started": kept, "kept_logged": kept_logged}


def delete_closure(closure_id, actor):
    if actor is None or not actor.is_active or actor.role not in {UserRole.ADMIN, UserRole.HOD}:
        raise OccurrenceError("forbidden", "an active HOD or ADMIN is required", 403)
    lock_writes()
    closure = CalendarClosure.query.filter_by(id=closure_id).with_for_update().first()
    if closure is None:
        raise OccurrenceError("closure_not_found", "closure not found", 404)
    if actor.role == UserRole.HOD and (closure.scope != "DEPARTMENT" or
                                     not actor.department or closure.department != actor.department):
        raise OccurrenceError("out_of_department", "closure is outside the HOD department", 403)
    audit(actor.id, "CLOSURE_DELETE", closure.date_from, closure.date_to, closure.reason,
          closure_id=closure.id, details={"scope": closure.scope, "department": closure.department})
    db.session.delete(closure)
    # Next startup/daily generation restores future dates only, not today/past.


def editable(occurrence_id, actor):
    if actor is None or not actor.is_active or actor.role not in {UserRole.FACULTY, UserRole.HOD}:
        raise OccurrenceError("forbidden", "an active FACULTY or HOD is required", 403)
    lock_writes()
    row = ScheduledClass.query.filter_by(id=occurrence_id).populate_existing().with_for_update().first()
    if row is None:
        raise OccurrenceError("occurrence_not_found", "occurrence not found", 404)
    if actor.role == UserRole.FACULTY:
        if row.professor_id != actor.id:
            raise OccurrenceError("out_of_department", "faculty may change only their own occurrence", 403)
    elif not actor.department or department_of(row) != actor.department:
        raise OccurrenceError("out_of_department", "occurrence is outside the HOD department", 403)
    if has_session(row):
        raise OccurrenceError("session_already_started", "an attendance session already exists", 409)
    if local_date(row.start_time) < today():
        raise OccurrenceError("occurrence_in_past", "a past occurrence cannot be changed", 409)
    if SessionLog.query.filter_by(class_id=row.id).first() is not None:
        raise OccurrenceError("occurrence_already_logged", "the occurrence already has immutable history", 409)
    if row.term_id is not None:
        # Pattern import/copy also lock the term before their clash snapshot.
        Term.query.filter_by(id=row.term_id).with_for_update().first()
    return row


def cancel(occurrence_id, actor, reason):
    row = editable(occurrence_id, actor)
    reason = reason_text(reason)
    if not is_cancelled(row):
        day = local_date(row.start_time)
        audit(actor.id, "CANCEL", day, day, reason, occurrence_id=row.id, slot_id=row.slot_id,
              details={"occurrence": row.to_dict()})
        db.session.flush()
    return row


def move(occurrence_id, actor, body):
    row = editable(occurrence_id, actor)
    reason = reason_text(body.get("reason"))
    if is_cancelled(row):
        raise OccurrenceError("occurrence_cancelled", "a cancelled occurrence cannot be moved", 409)
    for key in ("day_of_week", "period_id", "room_id"):
        if isinstance(body.get(key), bool) or not isinstance(body.get(key), int):
            raise OccurrenceError("invalid_fields", f"{key} must be an integer")
    weekday = body["day_of_week"]
    if weekday not in range(5):
        raise OccurrenceError("invalid_fields", "day_of_week must be 0 (Monday) through 4 (Friday)")
    period = db.session.get(Period, body["period_id"])
    room = db.session.get(Room, body["room_id"])
    if period is None:
        raise OccurrenceError("period_not_found", "unknown period")
    if room is None:
        raise OccurrenceError("room_unknown", "unknown room")
    faculty = db.session.get(User, row.professor_id)
    if faculty is None or faculty.role != UserRole.FACULTY or not faculty.is_active:
        raise OccurrenceError("faculty_not_faculty", "occurrence faculty must be an active FACULTY user")
    course = row.slot.course if row.slot else Course.query.filter(
        db.func.lower(Course.code) == row.course_code.lower()).first()
    if course is None:
        raise OccurrenceError("course_unknown", "unknown course")
    original_day = local_date(row.start_time)
    day = original_day - timedelta(days=original_day.weekday()) + timedelta(days=weekday)
    if day < today():
        raise OccurrenceError("occurrence_in_past", "the destination date is in the past", 409)
    term = db.session.get(Term, row.term_id) if row.term_id else None
    if term is not None and not term.start_date <= day <= term.end_date:
        raise OccurrenceError("outside_term", "destination is outside the occurrence's term")
    if matching_closures(day, department_of(row)):
        raise OccurrenceError("calendar_closed", "destination is inside a calendar closure", 409)
    start, end = period_times(day, period)
    warnings = []
    day_start, day_end = utc_bounds(day, day)
    others = ScheduledClass.query.filter(ScheduledClass.id != row.id,
        ScheduledClass.start_time >= day_start, ScheduledClass.start_time < day_end,
        ~ScheduledClass.id.in_(cancelled_ids())).all()
    # Complete the dated snapshot from the weekly pattern where no occurrence
    # was materialised yet (notably today or beyond the rolling horizon).
    represented = {r.slot_id for r in ScheduledClass.query.filter(
        ScheduledClass.start_time >= day_start, ScheduledClass.start_time < day_end).all()}
    represented |= {slot_id for slot_id, consumed_day in exception_keys() if consumed_day == day}
    if row.slot_id:
        represented.add(row.slot_id)
    future_slots = TimetableSlot.query.join(TermOffering).join(Term).filter(
        TimetableSlot.day_of_week == weekday, Term.start_date <= day, Term.end_date >= day).all()
    for slot in future_slots:
        if slot.id in represented or matching_closures(day, slot.offering.section.department):
            continue
        slot_start, slot_end = period_times(day, slot.period)
        virtual = ScheduledClass(slot_id=slot.id, term_id=slot.offering.term_id, slot=slot,
                                 professor_id=slot.faculty_id, course_code=slot.course.code,
                                 room=slot.room.name, start_time=slot_start, end_time=slot_end)
        others.append(virtual)
    for other in others:
        if matching_closures(day, department_of(other)) and (other.id is None or not has_session(other)):
            continue
        other_end = other.end_time or other.start_time + timedelta(minutes=50)
        if start >= other_end or end <= other.start_time:
            continue
        # Across offerings: section collisions are warnings, as in pattern validation.
        same_offering = row.slot and other.slot and row.slot.offering_id == other.slot.offering_id
        if same_offering:
            warnings.append({"error": "section_clash", "occurrence_id": other.id})
        else:
            if other.professor_id == row.professor_id:
                raise OccurrenceError("teacher_clash", "faculty already assigned at the destination", 409)
            if other.room.casefold() == room.name.casefold():
                raise OccurrenceError("room_clash", "room already assigned at the destination", 409)
        if row.slot_id is not None and other.slot_id == row.slot_id:
            raise OccurrenceError("section_clash", "another date of this slot already occupies the destination", 409)
    if course.is_lab != (room.type == "LAB"):
        warnings.append({"error": "lab_room_mismatch"})
    before = row.to_dict()
    row.day_of_week, row.start_time, row.end_time, row.room = weekday, start, end, room.name
    # Do not move the other half of a lab; expose the exception for the UI.
    if row.session_group_id:
        row.session_group_id = None
        warnings.append({"error": "lab_group_split"})
    audit(actor.id, "MOVE", original_day, day, reason, occurrence_id=row.id, slot_id=row.slot_id,
          details={"before": before, "after": row.to_dict(), "warnings": warnings})
    db.session.flush()
    return row, warnings


def derived_fields(row):
    """Display-only state. Mirrored outcomes retain their original contract value."""
    day = local_date(row.start_time)
    closures = matching_closures(day, department_of(row))
    log = SessionLog.query.filter_by(class_id=row.id).order_by(SessionLog.id.desc()).first()
    state, late, minutes = "SCHEDULED", False, None
    starts = first_session_starts([row])
    if row.id in starts:
        late, minutes = lateness(row.start_time, starts[row.id])
    if is_cancelled(row):
        state = "CANCELLED"
    elif log:
        if log.status == STATUS_CONDUCTED_ZERO_STUDENTS:
            state = "CONDUCTED_ZERO"
        elif log.status == STATUS_CONDUCTED:
            state = "CONDUCTED_LATE" if late else "CONDUCTED"
        elif log.status == STATUS_UNCONDUCTED:
            state = "UNCONDUCTED"
    elif row.end_time and row.end_time < utcnow() and row.id not in starts and not closures:
        state = "UNCONDUCTED"
    return {"state": state, "late": late, "lateMinutes": minutes,
            "date": day.isoformat(), "slotId": row.slot_id, "termId": row.term_id,
            "sessionGroupId": row.session_group_id,
            "holidayDeclaredAfterFact": bool(log and log.status == STATUS_UNCONDUCTED and
                                             any(c.created_at > log.timestamp for c in closures))}


def holiday_entries(professor_id=None, day_from=None, day_to=None):
    """Virtual cells for deleted/skipped rows, returned separately from real IDs.

    Defaults to the rolling future window; explicit ISO date filters on class
    reads allow past closures to be inspected without regenerating any row.
    """
    first = today() if day_from is None else day_from
    last = first + timedelta(days=current_app.config["TIMETABLE_WINDOW_DAYS"]) if day_to is None else day_to
    if last < first or (last - first).days > 366:
        raise OccurrenceError("invalid_fields", "holiday read range must be ordered and at most 366 days")
    closures = CalendarClosure.query.filter(CalendarClosure.date_from <= last,
                                            CalendarClosure.date_to >= first).all()
    if not closures:
        return []
    query = TimetableSlot.query.join(TermOffering)
    if professor_id is not None:
        query = query.filter(TimetableSlot.faculty_id == professor_id)
    slots = query.all()
    start, end = utc_bounds(first, last)
    existing = {(r.slot_id, local_date(r.start_time)) for r in ScheduledClass.query.filter(
        ScheduledClass.start_time >= start, ScheduledClass.start_time < end).all()}
    existing |= exception_keys()
    entries = []
    # Deleted snapshots preserve moved dates and legacy rows without slot_id;
    # pattern-derived cells below cover dates that were skipped at generation.
    active_ids = {c.id for c in closures}
    for entry in OccurrenceAudit.query.filter(OccurrenceAudit.action == "CLOSURE_CREATE",
                                              OccurrenceAudit.closure_id.in_(active_ids)).all():
        for snapshot in entry.details.get("removed_occurrences", []):
            instant = datetime.fromisoformat(snapshot["startTime"].replace("Z", "+00:00"))
            day = local_date(instant)
            if not first <= day <= last or (professor_id is not None and snapshot["professorId"] != professor_id):
                continue
            closed = matching_closures(day, snapshot.get("department"), closures)
            if not closed or db.session.get(ScheduledClass, snapshot["id"]) is not None:
                continue
            if OccurrenceAudit.query.filter_by(action="CANCEL", occurrence_id=snapshot["id"]).first():
                continue  # cancellation takes precedence and is exposed separately
            entries.append(dict(snapshot, id=None, originalOccurrenceId=snapshot["id"], state="HOLIDAY",
                                date=day.isoformat(), closureIds=[c.id for c in closed]))
            existing.add((snapshot["slotId"], day))
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for slot in slots:
            term = slot.offering.term
            if day.weekday() != slot.day_of_week or not term.start_date <= day <= term.end_date:
                continue
            closed = matching_closures(day, slot.offering.section.department, closures)
            if not closed or (slot.id, day) in existing:
                continue
            start, end = period_times(day, slot.period)
            entries.append({"id": None, "slotId": slot.id, "termId": term.id, "state": "HOLIDAY",
                            "date": day.isoformat(), "professorId": slot.faculty_id,
                            "courseCode": slot.course.code, "room": slot.room.name,
                            "startTime": start.isoformat() + "Z", "endTime": end.isoformat() + "Z",
                            "dayOfWeek": day.weekday(), "sessionGroupId": slot.session_group_id,
                            "closureIds": [c.id for c in closed]})
    return entries


def deleted_cancellations(professor_id=None, day_from=None, day_to=None):
    """Cancellation remains visible even if a subsequent closure removed its row."""
    first = today() if day_from is None else day_from
    last = first + timedelta(days=current_app.config["TIMETABLE_WINDOW_DAYS"]) if day_to is None else day_to
    if last < first or (last - first).days > 366:
        raise OccurrenceError("invalid_fields", "exception read range must be ordered and at most 366 days")
    entries = []
    for entry in OccurrenceAudit.query.filter(OccurrenceAudit.action == "CANCEL",
            OccurrenceAudit.date_from >= first, OccurrenceAudit.date_from <= last).all():
        snapshot = entry.details.get("occurrence")
        if not snapshot or db.session.get(ScheduledClass, entry.occurrence_id) is not None:
            continue
        if professor_id is not None and snapshot["professorId"] != professor_id:
            continue
        entries.append(dict(snapshot, id=None, originalOccurrenceId=entry.occurrence_id,
                            state="CANCELLED", date=entry.date_from.isoformat()))
    return entries
