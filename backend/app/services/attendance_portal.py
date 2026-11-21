"""Phase 2.9: one scoped reporting dataset for JSON, matrix and downloads.

Only build_report queries attendance data. Serializers consume its ordered rows;
verification only reads AttendanceLedger, including absent cells to detect lost
mirror rows. No reporting function writes or resolves a class outcome.
"""

import csv
import io
from datetime import date, datetime, timedelta, timezone

from flask import current_app
from openpyxl import Workbook
from sqlalchemy import String, and_, cast, literal, or_
from sqlalchemy.orm import aliased

from ..extensions import db
from ..models import (
    AttendanceCheckIn, AttendanceSession, CalendarClosure, Course, OccurrenceAudit,
    Period, ScheduledClass, Section, SessionLog, StudentProfile, StudentSection,
    Term, TermOffering, TimetableSlot, User, UserRole, WorkloadSession,
)
from ..models.workload import STATUS_CONDUCTED, STATUS_CONDUCTED_ZERO_STUDENTS, STATUS_UNCONDUCTED
from ..util import iso_z, utcnow
from . import blockchain, legacy_mapping, occurrences
from .timetable import lateness, timetable_timezone

STATUSES = {"SCHEDULED", "HOLIDAY", "CANCELLED", "CONDUCTED", "CONDUCTED_LATE",
            "CONDUCTED_ZERO", "UNCONDUCTED"}
CONDUCTED = {"CONDUCTED", "CONDUCTED_LATE", "CONDUCTED_ZERO"}
COLUMNS = ("studentId", "name", "rollNumber", "sectionId", "section", "present",
           "conducted", "scheduled", "percentage")
MIMES = {"csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ReportError(Exception):
    def __init__(self, error, message, status=400):
        super().__init__(message)
        self.error, self.message, self.status = error, message, status


def actor_for(user_id, role):
    actor = db.session.get(User, user_id)
    if (actor is None or not actor.is_active or actor.role != role or
            role not in {"FACULTY", "HOD"} or (role == "HOD" and not actor.department)):
        raise ReportError("out_of_scope", "attendance is limited to your own classes or department", 403)
    return actor


def integer(value, key, default=None, minimum=1, maximum=None):
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ReportError("invalid_filters", f"{key} must be an integer") from None
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ReportError("invalid_filters", f"{key} is outside the permitted range")
    return parsed


def filters_for(args):
    term_id = integer(args.get("term"), "term")
    term = db.session.get(Term, term_id) if term_id else Term.query.filter_by(is_active=True).first()
    if term is None:
        raise ReportError("term_not_found", "the requested or active term does not exist", 404)
    try:
        first = date.fromisoformat(args["date_from"]) if args.get("date_from") else term.start_date
        last = date.fromisoformat(args["date_to"]) if args.get("date_to") else term.end_date
    except (ValueError, TypeError):
        raise ReportError("invalid_filters", "date_from and date_to must be ISO dates") from None
    if last < first or (last - first).days > 366:
        raise ReportError("invalid_filters", "date range must be ordered and at most 366 days")
    status = str(args.get("status", "all")).upper()
    if status != "ALL" and status not in STATUSES:
        raise ReportError("invalid_filters", "unknown occurrence status")
    course = str(args.get("course") or "all").strip()
    q = str(args.get("q") or "").strip()
    if len(q) > 128:
        raise ReportError("invalid_filters", "student search must be at most 128 characters")
    return term, {"term": term.id, "section": integer(args.get("section"), "section"),
                  "course": course, "status": status, "date_from": first.isoformat(),
                  "date_to": last.isoformat(), "q": q}


def _matches_status(state, selected):
    # CONDUCTED is the outcome family, including late and zero-present variants.
    return selected == "ALL" or state == selected or (selected == "CONDUCTED" and state in CONDUCTED)


def _period_label(start, periods, zone):
    clock = start.replace(tzinfo=timezone.utc).astimezone(zone).time().replace(tzinfo=None)
    match = next((p for p in periods if p.start_time == clock), None)
    return f"P{match.ordinal}" if match else clock.strftime("%H:%M")


def _course_code(value):
    """Course filter as a code; "" means an unknown numeric course."""
    if value.casefold() == "all":
        return None
    if value.isdecimal():
        course = db.session.get(Course, int(value))
        return course.code if course else ""
    return value


def _refuse_unmapped(actor, term, start, end, course_code):
    """Fail explicitly instead of silently dropping slotless occurrences.

    FACULTY only ever sees the ids of their own classes, so naming them is not
    a cross-user signal. HOD is handled by ``refuse_hod_reports`` before any
    row or filter is read, because a per-range HOD answer would confirm whether
    the requested range holds legacy rows.
    """
    if actor.role != UserRole.FACULTY:
        return
    rows = legacy_mapping.unmapped_occurrences(
        start, end, term_id=term.id, professor_id=actor.id, course_code=course_code)
    if not rows:
        return
    ids = ", ".join(str(row.id) for row in rows[:20])
    if len(rows) > 20:
        ids += ", ..."
    raise ReportError(
        "legacy_scope_unavailable",
        f"Unmapped slotless occurrence(s) in this range: {ids}. An operator must record "
        "the authoritative section mapping; no association has been inferred or backfilled.",
        409)


def refuse_hod_reports(actor):
    """Disable HOD attendance reports while ANY legacy row is unmapped.

    Called at the API boundary before any occurrence row or filter is read, and
    again by ``build_report`` as defence in depth. The gate is global, so every
    HOD request (any range, any section, any occurrence id, existing or not)
    receives byte-identical text: no occurrence ids, no counts and no hint that
    a particular range or id holds legacy rows. It lifts only when the
    authoritative mapping backlog is empty.
    """
    if actor.role != UserRole.HOD:
        return
    if legacy_mapping.any_unmapped():
        raise ReportError(
            "legacy_scope_unavailable",
            "Attendance reports are unavailable while slotless legacy occurrences "
            "without an authoritative section mapping remain. An operator must map "
            "them before department attendance reports can be produced.",
            409)


def _legacy_meetings(rows, meetings, periods, zone):
    """Merge actively mapped slotless occurrences into the same dataset.

    Section and department come only from the operator's mapping; the
    occurrence id and every attendance-session id stay unchanged.
    """
    if not rows:
        return rows
    ids = [row.id for row, _section in rows]
    names = [str(value) for value in ids] + [f"workload-{value}" for value in ids]
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    records = db.session.query(AttendanceSession, AttendanceCheckIn, WorkloadSession).outerjoin(
        AttendanceCheckIn, AttendanceCheckIn.session_id == AttendanceSession.id).outerjoin(
        WorkloadSession, WorkloadSession.attendance_session_id == AttendanceSession.id).filter(
        AttendanceSession.class_id.in_(names)).order_by(
        AttendanceSession.id, AttendanceCheckIn.id).all()
    logs = SessionLog.query.filter(SessionLog.class_id.in_(ids)).all()
    by_class, logs_by_class = {}, {}
    for session, capture, work in records:
        by_class.setdefault(session.class_id, []).append((session, capture, work))
    for log in logs:
        logs_by_class.setdefault(log.class_id, []).append(log)
    for row, section in rows:
        label = section.to_dict()["label"]
        meeting = meetings.setdefault(row.id, {
            "slotId": None, "sectionId": section.id, "section": label,
            "courseCode": row.course_code, "department": section.department,
            "period": _period_label(row.start_time, periods.values(), zone),
            "facultyId": row.professor_id, "occurrenceId": row.id,
            "key": f"occurrence:{row.id}",
            "date": occurrences.local_date(row.start_time).isoformat(),
            "startTime": iso_z(row.start_time),
            "endTime": iso_z(row.end_time) if row.end_time else None,
            "_row": row, "_sessions": {}, "_captures": {}, "_logs": {},
        })
        for session, capture, work in (by_class.get(str(row.id), []) +
                                       by_class.get(f"workload-{row.id}", [])):
            if session.start_time < row.start_time - grace:
                continue  # a retained session from a recycled class id
            meeting["_sessions"][session.id] = work.started_at if work else session.start_time
            if capture is not None:
                meeting["_captures"][(capture.student_id, capture.session_id)] = capture
        for log in logs_by_class.get(row.id, []):
            meeting["_logs"][log.id] = log
    return rows


def build_report(actor, term, filters, *, roster=False):
    """Build the entire filtered dataset once; paging happens only afterwards.

    A single joined attendance query includes the permanent roster (including
    absentees), both attendance-session namespaces, captures and outcome logs.
    Closure/audit reads are exception metadata, not a second attendance path.
    Student search is applied AFTER class status/counts, never to denominators.
    """
    # Before any filter is read: the HOD answer must not depend on the range.
    refuse_hod_reports(actor)
    zone, now = timetable_timezone(), utcnow()
    first, last = (date.fromisoformat(filters[key]) for key in ("date_from", "date_to"))
    start, end = occurrences.utc_bounds(first, last)
    course_code = _course_code(filters["course"])
    # Only operator decisions, never an inferred association. An unmapped row
    # cannot be placed in a section, so the report refuses instead of omitting it.
    legacy_rows = legacy_mapping.active_occurrences(
        term.id, start, end,
        professor_id=actor.id if actor.role == UserRole.FACULTY else None,
        department=actor.department if actor.role == UserRole.HOD else None,
        section_id=filters["section"], course_code=course_code)
    # A faculty member's mapped legacy sections supply the roster rows (and
    # therefore the denominators) that the slot-free offering cannot join.
    mapped_sections = {section.id for _row, section in legacy_rows}
    roster_user = aliased(User)
    class_key = cast(ScheduledClass.id, String)
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    occurrence_on = [ScheduledClass.slot_id == TimetableSlot.id, ScheduledClass.term_id == term.id,
                     ScheduledClass.start_time >= start, ScheduledClass.start_time < end]
    if actor.role == UserRole.FACULTY:
        occurrence_on.append(ScheduledClass.professor_id == actor.id)
    query = db.session.query(
        TimetableSlot, TermOffering, Section, Course, Period, StudentSection, roster_user,
        StudentProfile, ScheduledClass, AttendanceSession, AttendanceCheckIn, SessionLog, WorkloadSession,
    ).select_from(TermOffering).join(Section, TermOffering.section_id == Section.id).outerjoin(
        TimetableSlot, TimetableSlot.offering_id == TermOffering.id).outerjoin(
        Course, TimetableSlot.course_id == Course.id).outerjoin(
        Period, TimetableSlot.period_id == Period.id).outerjoin(
        StudentSection, StudentSection.section_id == Section.id).outerjoin(
        roster_user, and_(roster_user.id == StudentSection.user_id, roster_user.role == UserRole.STUDENT)).outerjoin(
        StudentProfile, StudentProfile.user_id == roster_user.id).outerjoin(
        ScheduledClass, and_(*occurrence_on)).outerjoin(
        AttendanceSession, and_(or_(AttendanceSession.class_id == class_key,
                                    AttendanceSession.class_id == literal("workload-") + class_key),
                                AttendanceSession.start_time >= ScheduledClass.start_time - grace)).outerjoin(
        AttendanceCheckIn, and_(AttendanceCheckIn.session_id == AttendanceSession.id,
                                AttendanceCheckIn.student_id == roster_user.id)).outerjoin(
        SessionLog, SessionLog.class_id == ScheduledClass.id).outerjoin(
        WorkloadSession, and_(WorkloadSession.class_id == ScheduledClass.id,
                              WorkloadSession.attendance_session_id == AttendanceSession.id)).filter(
        TermOffering.term_id == term.id)
    if actor.role == UserRole.HOD:
        query = query.filter(Section.department == actor.department)
    else:
        scope = [TimetableSlot.faculty_id == actor.id, ScheduledClass.professor_id == actor.id]
        if mapped_sections:
            # Roster rows only: the section's classes are filtered out below.
            scope.append(Section.id.in_(mapped_sections))
        query = query.filter(or_(*scope))
    if filters["section"] is not None:
        section = db.session.get(Section, filters["section"])
        if section is None or (actor.role == UserRole.HOD and section.department != actor.department):
            raise ReportError("out_of_scope", "section is outside your attendance scope", 403)
        query = query.filter(Section.id == section.id)
    if filters["course"].casefold() != "all":
        course = filters["course"]
        query = query.filter(Course.id == int(course) if course.isdecimal() else
                             db.func.lower(Course.code) == course.casefold())
    if not roster:
        _refuse_unmapped(actor, term, start, end, course_code)
    records = query.order_by(Section.id, TimetableSlot.id, ScheduledClass.start_time,
                            ScheduledClass.id, roster_user.id, AttendanceSession.id,
                            AttendanceCheckIn.id, SessionLog.id).all()
    slots, students, meetings = {}, {}, {}
    # Canonical period metadata also resolves moved occurrences to P1..P6.
    periods = {p.id: p for p in Period.query.filter_by(is_break=False).all()}
    for slot, offering, section, course, period, membership, user, profile, row, session, capture, log, work in records:
        label = section.to_dict()["label"]
        if user is not None:
            students[user.id] = {"studentId": user.id, "name": user.full_name,
                                 "rollNumber": profile.roll_number if profile and profile.roll_number else "",
                                 "sectionId": section.id, "section": label}
        if slot is None:  # HOD may read their department's blank offering roster.
            continue
        periods[period.id] = period
        base = {"slotId": slot.id, "sectionId": section.id, "section": label,
                "courseCode": course.code, "department": section.department,
                "period": f"P{period.ordinal}", "facultyId": slot.faculty_id}
        slots[slot.id] = (slot, base)
        if row is None:
            continue
        meeting = meetings.setdefault(row.id, dict(base, occurrenceId=row.id, key=f"occurrence:{row.id}",
            date=occurrences.local_date(row.start_time).isoformat(),
            startTime=iso_z(row.start_time), endTime=iso_z(row.end_time) if row.end_time else None,
            facultyId=row.professor_id, courseCode=row.course_code, _row=row,
            _sessions={}, _captures={}, _logs={}))
        if session:
            meeting["_sessions"][session.id] = work.started_at if work else session.start_time
        if capture:
            meeting["_captures"][(capture.student_id, capture.session_id)] = capture
        if log:
            meeting["_logs"][log.id] = log
    _legacy_meetings(legacy_rows, meetings, periods, zone)
    if actor.role == UserRole.FACULTY and mapped_sections:
        # A mapped section contributes its roster (denominators), never a
        # colleague's classes that happen to sit in the same section.
        legacy_ids = {row.id for row, _section in legacy_rows}
        slots = {slot_id: value for slot_id, value in slots.items()
                 if value[0].faculty_id == actor.id}
        meetings = {key: meeting for key, meeting in meetings.items()
                    if meeting["occurrenceId"] in legacy_ids or meeting["facultyId"] == actor.id}
    if not records and not legacy_rows and (
            filters["section"] is not None or filters["course"].casefold() != "all"):
        raise ReportError("out_of_scope", "the requested section/course has no classes in your scope", 403)
    closures = CalendarClosure.query.filter(CalendarClosure.date_from <= last,
                                            CalendarClosure.date_to >= first).all()
    if actor.role == UserRole.HOD:
        closures = [c for c in closures if c.scope == "UNIVERSITY" or c.department == actor.department]
    audits = OccurrenceAudit.query.filter(OccurrenceAudit.date_from <= last,
        OccurrenceAudit.date_to >= first, OccurrenceAudit.action.in_(["CANCEL", "MOVE", "CLOSURE_CREATE"])).all()
    cancelled = {a.occurrence_id for a in audits if a.action == "CANCEL"}
    consumed = {(a.slot_id, a.date_from) for a in audits if a.action in {"CANCEL", "MOVE"}}
    represented = {(m["slotId"], date.fromisoformat(m["date"])) for m in meetings.values()} | consumed
    for meeting in meetings.values():
        row = meeting["_row"]
        closed = occurrences.matching_closures(date.fromisoformat(meeting["date"]), meeting["department"], closures)
        logs = meeting["_logs"]
        log = logs[max(logs)] if logs else None
        starts = meeting["_sessions"].values()
        actual = min(starts) if starts else None
        late, minutes = lateness(row.start_time, actual) if actual else (False, None)
        state = "SCHEDULED"
        if row.id in cancelled:
            state = "CANCELLED"
        elif closed and actual is None and log is None:
            state = "HOLIDAY"
        elif row.start_time <= now:
            if log and log.status == STATUS_UNCONDUCTED:
                state = "UNCONDUCTED"
            elif log and log.status in {STATUS_CONDUCTED, STATUS_CONDUCTED_ZERO_STUDENTS}:
                state = "CONDUCTED_ZERO" if log.status == STATUS_CONDUCTED_ZERO_STUDENTS else (
                    "CONDUCTED_LATE" if late else "CONDUCTED")
            elif actual is not None and (row.end_time or row.start_time + timedelta(minutes=50)) <= now:
                # Read-only fallback: a completed, started class was held even
                # if the final sweep/mirror has not run. Includes zero captures.
                state = ("CONDUCTED_LATE" if late else "CONDUCTED") if meeting["_captures"] else "CONDUCTED_ZERO"
            elif actual is None and (row.end_time or row.start_time + timedelta(minutes=50)) <= now:
                state = "UNCONDUCTED"
        # A closure cannot erase retained started/logged evidence (D15).
        meeting.update(status=state, lateMinutes=minutes if actual else None,
                       period=_period_label(row.start_time, periods.values(), zone),
                       holidayDeclaredAfterFact=bool(log and log.status == STATUS_UNCONDUCTED and
                                                    any(c.created_at > log.timestamp for c in closed)))
    # Deleted cancellations and closure snapshots preserve dated moves. Never
    # regenerate any row. Virtual holidays have a stable key and no real ID.
    for audit in audits:
        snapshots = ([audit.details.get("occurrence")] if audit.action == "CANCEL" else
                     audit.details.get("removed_occurrences", []) if audit.action == "CLOSURE_CREATE" else [])
        for snapshot in snapshots:
            if not snapshot or snapshot.get("slotId") not in slots or snapshot["id"] in meetings:
                continue
            instant = datetime.fromisoformat(snapshot["startTime"].replace("Z", "+00:00"))
            day = occurrences.local_date(instant)
            if not first <= day <= last or snapshot.get("termId") != term.id:
                continue
            if actor.role == UserRole.FACULTY and snapshot["professorId"] != actor.id:
                continue
            slot, base = slots[snapshot["slotId"]]
            state = "CANCELLED" if snapshot["id"] in cancelled else "HOLIDAY"
            if state == "HOLIDAY" and not occurrences.matching_closures(day, base["department"], closures):
                continue
            key = f"{state.lower()}:{slot.id}:{day.isoformat()}"
            meetings[key] = dict(base, occurrenceId=None, originalOccurrenceId=snapshot["id"], key=key,
                date=day.isoformat(), startTime=snapshot["startTime"], endTime=snapshot.get("endTime"),
                facultyId=snapshot["professorId"], courseCode=snapshot["courseCode"],
                period=_period_label(instant.replace(tzinfo=None), periods.values(), zone),
                status=state, lateMinutes=None, _sessions={}, _captures={})
            represented.add((slot.id, day))
    for slot, base in slots.values():
        if actor.role == UserRole.FACULTY and slot.faculty_id != actor.id:
            continue
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            if (not term.start_date <= day <= term.end_date or day.weekday() != slot.day_of_week or
                    (slot.id, day) in represented or not occurrences.matching_closures(day, base["department"], closures)):
                continue
            slot_start, slot_end = occurrences.period_times(day, periods[slot.period_id])
            key = f"holiday:{slot.id}:{day.isoformat()}"
            meetings[key] = dict(base, occurrenceId=None, key=key, date=day.isoformat(),
                startTime=iso_z(slot_start), endTime=iso_z(slot_end), status="HOLIDAY", lateMinutes=None,
                _sessions={}, _captures={})
    roster_sizes = {}
    for student in students.values():
        section_id = student["sectionId"]
        roster_sizes[section_id] = roster_sizes.get(section_id, 0) + 1
    meetings = sorted((m for m in meetings.values() if _matches_status(m["status"], filters["status"])),
                      key=lambda m: (m["startTime"], m["section"], m["courseCode"], m["key"]))
    q = filters["q"].casefold()
    visible = sorted((s for s in students.values() if not q or q in s["name"].casefold() or
                      q in s["rollNumber"].casefold()), key=lambda s: (s["rollNumber"], s["name"], s["studentId"]))
    visible_ids = {s["studentId"] for s in visible}
    if q:
        # Search limits the section/session set as well as the student rows.
        visible_sections = {s["sectionId"] for s in visible}
        meetings = [m for m in meetings if m["sectionId"] in visible_sections]
    for meeting in meetings:
        meeting["total"] = roster_sizes.get(meeting["sectionId"], 0)
        # Session counters show captures, including an in-progress session;
        # only the conducted subset below can affect a student's figures.
        meeting["present"] = len({sid for sid, _ in meeting["_captures"] if sid in visible_ids})
    rows = []
    for student in visible:
        selected = [m for m in meetings if m["sectionId"] == student["sectionId"]]
        counted = [m for m in selected if m["status"] in CONDUCTED]
        present = sum(any(sid == student["studentId"] for sid, _ in m["_captures"]) for m in counted)
        conducted = len(counted)
        rows.append(dict(student, present=present, conducted=conducted,
                         scheduled=sum(m["status"] not in {"HOLIDAY", "CANCELLED"} for m in selected),
                         percentage=round(present / conducted * 100, 1) if conducted else None,
                         cells=[{"sessionKey": m["key"], "occurrenceId": m["occurrenceId"],
                                 "status": m["status"], "attendance": (
                                     "PRESENT" if any(sid == student["studentId"] for sid, _ in m["_captures"])
                                     else "ABSENT") if m["status"] in CONDUCTED else None} for m in selected]))
    metadata = {"filters": filters, "term": term.to_dict(),
                "generatedAt": now.replace(tzinfo=timezone.utc).astimezone(zone).isoformat(),
                "timezone": str(zone), "requestedBy": {"id": actor.id, "name": actor.full_name}}
    return {"rows": rows, "sessions": meetings, "metadata": metadata}


def page_report(report, offset=0, limit=50, matrix=False):
    rows = report["rows"][offset:offset + limit]
    if not matrix:
        rows = [{key: row[key] for key in COLUMNS} for row in rows]
    return dict(report["metadata"], rows=rows, total=len(report["rows"]), offset=offset, limit=limit,
                **({"sessions": [session_payload(m) for m in report["sessions"]]} if matrix else {}))


def session_payload(meeting):
    return {key: value for key, value in meeting.items() if not key.startswith("_") and key != "department"}


def roster_payload(report, occurrence_id):
    meeting = next((m for m in report["sessions"] if m["occurrenceId"] == occurrence_id), None)
    if meeting is None:
        raise ReportError("out_of_scope", "occurrence is outside your attendance scope or filters", 403)
    students = {s["studentId"]: s for s in report["rows"]}
    captures = sorted(meeting["_captures"].values(), key=lambda c: (c.checked_in_at, c.id))
    rows = [{"studentId": c.student_id, "sessionId": c.session_id,
             "name": students[c.student_id]["name"], "rollNumber": students[c.student_id]["rollNumber"],
             "timestamp": iso_z(c.checked_in_at), "method": c.method, "deviceVerified": c.device_verified}
            for c in captures if c.student_id in students and (
                meeting["sectionId"] is None or
                students[c.student_id].get("sectionId") == meeting["sectionId"])]
    return {"session": session_payload(meeting), "rows": rows}


def legacy_roster_payload(actor, occurrence, args):
    """Read known captures of an owned legacy class, never infer its cohort."""
    if actor.role != UserRole.FACULTY or occurrence.professor_id != actor.id:
        raise ReportError("out_of_scope", "occurrence is outside your attendance scope", 403)
    term_id = integer(args.get("term"), "term")
    section_id = integer(args.get("section"), "section")
    status = str(args.get("status", "all")).upper()
    if status != "ALL" and status not in STATUSES:
        raise ReportError("invalid_filters", "unknown occurrence status")
    if section_id is not None or status != "ALL":
        raise ReportError("legacy_scope_unavailable",
            "This slotless occurrence has no section association; section/status filtering is unavailable.", 409)
    if term_id is not None and occurrence.term_id is None:
        raise ReportError("legacy_scope_unavailable", "This legacy occurrence has no term association.", 409)
    if term_id is not None and term_id != occurrence.term_id:
        raise ReportError("out_of_scope", "occurrence is outside the requested term", 403)
    day = occurrences.local_date(occurrence.start_time)
    try:
        first = date.fromisoformat(args["date_from"]) if args.get("date_from") else day
        last = date.fromisoformat(args["date_to"]) if args.get("date_to") else day
    except (ValueError, TypeError):
        raise ReportError("invalid_filters", "date_from and date_to must be ISO dates") from None
    if last < first or (last - first).days > 366:
        raise ReportError("invalid_filters", "date range must be ordered and at most 366 days")
    if not first <= day <= last:
        raise ReportError("out_of_scope", "occurrence is outside the requested date range", 403)
    course = str(args.get("course") or "all").strip()
    if course.casefold() != "all":
        mapped = db.session.get(Course, int(course)) if course.isdecimal() else None
        code = mapped.code if mapped else course
        if (course.isdecimal() and mapped is None) or code.casefold() != occurrence.course_code.casefold():
            raise ReportError("out_of_scope", "occurrence is outside the requested course", 403)
    q = str(args.get("q") or "").strip().casefold()
    if len(q) > 128:
        raise ReportError("invalid_filters", "student search must be at most 128 characters")
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    records = db.session.query(AttendanceCheckIn, User, StudentProfile).join(
        AttendanceSession, AttendanceSession.id == AttendanceCheckIn.session_id).outerjoin(
        User, User.id == AttendanceCheckIn.student_id).outerjoin(
        StudentProfile, StudentProfile.user_id == User.id).filter(
        AttendanceSession.class_id.in_([str(occurrence.id), f"workload-{occurrence.id}"]),
        AttendanceSession.start_time >= occurrence.start_time - grace).order_by(
        AttendanceCheckIn.checked_in_at, AttendanceCheckIn.id).all()
    students, captures = {}, {}
    for capture, user, profile in records:
        name = user.full_name if user else None
        roll = profile.roll_number if profile else None
        if q and q not in (name or "").casefold() and q not in (roll or "").casefold():
            continue
        students[capture.student_id] = {"studentId": capture.student_id, "name": name, "rollNumber": roll}
        captures[(capture.student_id, capture.session_id)] = capture
    meeting = {"occurrenceId": occurrence.id, "slotId": None, "termId": occurrence.term_id,
               "sectionId": None, "section": None, "facultyId": occurrence.professor_id,
               "date": day.isoformat(), "courseCode": occurrence.course_code,
               "startTime": iso_z(occurrence.start_time),
               "endTime": iso_z(occurrence.end_time) if occurrence.end_time else None,
               "legacy": True, "total": None, "_captures": captures}
    return roster_payload({"sessions": [meeting], "rows": list(students.values())}, occurrence.id)


def verify_report(report, offset=0, limit=50):
    """Recount visible student/occurrence cells, including missing local captures.

    AttendanceLedger stores presence only, not outcome, method or device flags.
    Multiple session namespaces still count as ONE occurrence for percentages.
    """
    verified, mismatches = 0, []
    for student in report["rows"][offset:offset + limit]:
        for meeting in report["sessions"]:
            if meeting["sectionId"] != student["sectionId"] or meeting["status"] not in CONDUCTED:
                continue
            sid = student["studentId"]
            for session_id in sorted(meeting["_sessions"]):
                mirrored = (sid, session_id) in meeting["_captures"]
                locked = blockchain.is_locked(sid, session_id)
                if mirrored != locked:
                    mismatches.append({"studentId": sid, "occurrenceId": meeting["occurrenceId"],
                                       "sessionId": session_id, "databasePresent": mirrored,
                                       "ledgerPresent": locked})
                elif locked:
                    verified += 1
    return {"verified": verified, "mismatches": mismatches}


def download(report, fmt):
    """Serialization ONLY: identical ordered rows for CSV and XLSX, no DB reads."""
    meta = report["metadata"]
    header = [("Term", meta["term"]["name"]), ("Generated at", meta["generatedAt"]),
              ("Timezone", meta["timezone"]), ("Requested by", meta["requestedBy"]["name"])]
    header += [(f"Filter: {key}", "all" if value is None else str(value)) for key, value in meta["filters"].items()]
    table = [[row[key] for key in COLUMNS] for row in report["rows"]]
    data = header + [[]] + [list(COLUMNS)] + table
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in data:
            writer.writerow([("'" + value) if isinstance(value, str) and
                             value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in row])
        return buffer.getvalue().encode("utf-8-sig")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    for row in data:
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = f"A{len(header) + 3}"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
