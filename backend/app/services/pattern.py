"""Weekly pattern planning: atomic upserts, whole-term clashes and lab pairs.

No occurrences or chain writes. All pattern writers lock the parent term to
serialize clash checks. Duplicate CSV cells warn and use the last row; the
unique database cell remains authoritative. Group IDs are deterministic.

Phase 2.6 read views build one complete week from the stored pattern, merge
lab pairs and report stored-pattern warnings with slot ids.
"""

import csv
import io
import re
import zlib
from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from ..extensions import db
from ..models import Course, FacultyProfile, Period, Room, Section, Term, TermOffering, TimetableSlot, User, UserRole

COLUMNS = ("section", "course_code", "faculty_employee_id", "room", "day", "period")
DAYS = ("MON", "TUE", "WED", "THU", "FRI")
FIELDS = ("offering_id", "course_id", "faculty_id", "room_id", "day_of_week", "period_id", "session_group_id")
# Fixed palette for legend colours. No red: conflicts are never signalled by colour.
COLOUR_TOKENS = ("sky", "emerald", "amber", "violet", "teal", "orange", "indigo", "lime", "fuchsia", "cyan")
# The name each view already shows in its heading is omitted from its cells.
VIEW_OMITS = {"class": "sectionLabel", "teacher": "facultyName", "room": "roomName"}


class PatternError(Exception):
    def __init__(self, error, message, status=400):
        super().__init__(message)
        self.error, self.message, self.status = error, message, status


def positive_id(raw, field):
    value = str(raw or "")
    if len(value) > 10 or not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2147483647:
        raise PatternError("invalid_fields", f"{field} must be a positive integer")
    return int(value)


def lock_term(term_id=None, *, lock=True):
    query = Term.query
    query = query.filter_by(id=term_id) if term_id else query.filter_by(is_active=True)
    if lock:
        query = query.populate_existing().with_for_update()
    term = query.first()
    if term is None:
        raise PatternError("offering_not_found", "no selected/active term; activate a term first", 403)
    return term


def _issue(code, message, *, line=None, status=400, **extra):
    return dict(check="timetable", error=code, message=message, line=line, status=status, **extra)


def _section_name(value):
    # Accept the model's display label, a plain 'BS CS 2023 A', or Section.id.
    return " ".join(re.sub(r"\s*[·]\s*", " ", value).split()).casefold()


def _snapshot(term_id):
    rows = TimetableSlot.query.join(TermOffering).filter(TermOffering.term_id == term_id).all()
    existing = {(row.offering_id, row.day_of_week, row.period_id): row for row in rows}
    state = {key: dict((field, getattr(row, field)) for field in FIELDS) for key, row in existing.items()}
    return existing, state


def _masters():
    return (
        {row.id: row for row in Course.query.all()},
        {row.id: row for row in Room.query.all()},
        {row.id: row for row in Period.query.all()},
    )


def _group_labs(state, courses, periods, affected):
    """Pair only two physically adjacent periods; P3/P4 straddle the break."""
    buckets = defaultdict(list)
    for key, value in state.items():
        if (value["offering_id"], value["day_of_week"]) not in affected:
            continue
        value["session_group_id"] = None
        course, period = courses.get(value["course_id"]), periods.get(value["period_id"])
        if course and course.is_lab and period and not period.is_break and period.ordinal is not None:
            identity = tuple(value[field] for field in ("offering_id", "day_of_week", "course_id", "faculty_id", "room_id"))
            buckets[identity].append((key, value))
    for identity, values in buckets.items():
        values.sort(key=lambda item: periods[item[1]["period_id"]].start_time)
        index = 0
        while index + 1 < len(values):
            first, second = values[index][1], values[index + 1][1]
            p1, p2 = periods[first["period_id"]], periods[second["period_id"]]
            if p2.ordinal == p1.ordinal + 1 and p1.end_time == p2.start_time:
                group = str(uuid5(NAMESPACE_URL, "eduledger/lab/" + repr((identity, p1.id, p2.id))))
                first["session_group_id"] = second["session_group_id"] = group
                index += 2
            else:
                index += 1


def _validate_term(state, courses, rooms, periods, lines):
    """Validate the final term snapshot, after ALL incoming cells are applied."""
    violations, warnings = [], []
    teachers, locations = defaultdict(list), defaultdict(list)
    faculty_ids = {value["faculty_id"] for value in state.values()}
    valid_faculty = {row.id for row in User.query.join(FacultyProfile).filter(
        User.id.in_(faculty_ids), User.role == UserRole.FACULTY, User.is_active.is_(True),
    ).all()}
    for key, value in state.items():
        line = lines.get(key)
        period = periods.get(value["period_id"])
        if period is None:
            violations.append(_issue("period_invalid", "period is unknown", line=line))
        elif period.is_break or period.ordinal is None:
            violations.append(_issue("break_period", "a slot cannot reference the break", line=line))
        elif period.ordinal not in range(1, 7):
            violations.append(_issue("period_invalid", "period must be 1..6", line=line))
        if value["faculty_id"] not in valid_faculty:
            violations.append(_issue("faculty_not_faculty", "faculty must be an active FACULTY user with a profile", line=line))
        course, room = courses.get(value["course_id"]), rooms.get(value["room_id"])
        if course is None:
            violations.append(_issue("course_unknown", "course is unknown", line=line))
        if room is None:
            violations.append(_issue("room_unknown", "room is unknown", line=line))
        if course and room and course.is_lab != (room.type == "LAB"):
            warnings.append(_issue("lab_room_mismatch", f"{course.code} does not match {room.type} room {room.name}",
                                   line=line, status=200, offering_id=value["offering_id"], period_id=value["period_id"]))
        teachers[(value["faculty_id"], value["day_of_week"], value["period_id"])].append(key)
        locations[(value["room_id"], value["day_of_week"], value["period_id"])].append(key)
    for code, buckets in (("teacher_clash", teachers), ("room_clash", locations)):
        for (resource_id, day, period_id), keys in sorted(buckets.items()):
            offerings = sorted({key[0] for key in keys})
            if len(offerings) > 1:
                source_lines = sorted({lines[key] for key in keys if key in lines})
                violations.append(_issue(code, f"{code}: resource {resource_id}, {DAYS[day]}, period {period_id}, offerings {offerings}",
                                         line=source_lines[0] if source_lines else None, status=409,
                                         lines=source_lines, offering_ids=offerings))
    return violations, warnings


def _finish(existing, state, touched, lines, violations, warnings):
    courses, rooms, periods = _masters()
    _group_labs(state, courses, periods, {(key[0], key[1]) for key in touched})
    hard, mismatch = _validate_term(state, courses, rooms, periods, lines)
    violations.extend(hard)
    warnings.extend(mismatch)
    created, updated, unchanged, changes = 0, 0, 0, []
    for key, value in sorted(state.items()):
        row = existing.get(key)
        if row is None:
            created += 1
            changes.append(dict(action="create", line=lines.get(key), **value))
        elif any(getattr(row, field) != value[field] for field in FIELDS):
            updated += 1
            changes.append(dict(action="update", id=row.id, line=lines.get(key), **value))
        elif key in touched:
            unchanged += 1
    report = dict(rows_created=created, rows_updated=updated, rows_unchanged=unchanged,
                  warnings=warnings, violations=violations, proposed_changes=changes)
    return report, existing, state


def plan_import(data, term, department):
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = next(reader, None)
    if header is None:
        raise PatternError("invalid_file", "CSV header is required")
    header = [cell.strip().lower() for cell in header]
    if {"start_time", "end_time"} & set(header):
        raise PatternError("legacy_format", "weekly pattern columns are: " + ",".join(COLUMNS) + "; start_time/end_time are not accepted")
    if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
        raise PatternError("invalid_file", "CSV must contain exactly: " + ",".join(COLUMNS))
    sections = Section.query.all()
    names = {_section_name(f"{row.programme} {row.batch_year} {row.letter}"): row for row in sections}
    ids = {str(row.id): row for row in sections}
    offerings = {row.section_id: row for row in TermOffering.query.filter_by(term_id=term.id).all()}
    courses = {row.code.lower(): row for row in Course.query.all()}
    rooms = {row.name.lower(): row for row in Room.query.all()}
    faculty = {profile.employee_id: user for profile, user in db.session.query(FacultyProfile, User).join(User).all()}
    periods = {row.ordinal: row for row in Period.query.all()}
    existing, state = _snapshot(term.id)
    touched, lines, violations, warnings = set(), {}, [], []
    count = 0
    for cells in reader:
        line = reader.line_num
        count += 1
        if count > 5000:
            raise PatternError("invalid_file", "CSV exceeds 5000 rows")
        if not cells or not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(header):
            violations.append(_issue("invalid_file", "row length does not match header", line=line))
            continue
        value = {key: cell.strip() for key, cell in zip(header, cells)}
        bad = []
        section = ids.get(value["section"]) or names.get(_section_name(value["section"]))
        offering = offerings.get(section.id) if section else None
        if offering is None:
            bad.append(_issue("offering_not_found", "section has no offering in this term", line=line, status=403))
        if section and section.department != department:
            bad.append(_issue("out_of_department", "HOD may import only their department's offerings", line=line, status=403))
        course, room = courses.get(value["course_code"].lower()), rooms.get(value["room"].lower())
        user = faculty.get(value["faculty_employee_id"])
        if course is None:
            bad.append(_issue("course_unknown", f"unknown course {value['course_code']}", line=line))
        if room is None:
            bad.append(_issue("room_unknown", f"unknown room {value['room']}", line=line))
        if user is None or user.role != UserRole.FACULTY or not user.is_active:
            bad.append(_issue("faculty_not_faculty", f"unknown/non-FACULTY employee {value['faculty_employee_id']}", line=line))
        day = value["day"].upper()
        if day not in DAYS:
            bad.append(_issue("day_invalid", "day must be MON..FRI", line=line))
        raw_period = value["period"]
        period = periods.get(int(raw_period)) if raw_period in {"1", "2", "3", "4", "5", "6"} else None
        if raw_period.upper() in {"BREAK", "NULL"} or (period and (period.is_break or period.ordinal is None)):
            bad.append(_issue("break_period", "a slot cannot reference the break", line=line))
        elif period is None:
            bad.append(_issue("period_invalid", "period must be a seeded teaching ordinal 1..6", line=line))
        if bad:
            violations.extend(bad)
            continue
        key = (offering.id, DAYS.index(day), period.id)
        assignment = dict(offering_id=offering.id, course_id=course.id, faculty_id=user.id, room_id=room.id,
                          day_of_week=DAYS.index(day), period_id=period.id, session_group_id=None)
        if key in touched:
            warnings.append(_issue("section_clash", "duplicate CSV cell; last row wins", line=line, status=200,
                                   previous_line=lines[key], offering_id=offering.id))
        elif key in existing and any(getattr(existing[key], field) != assignment[field]
                                    for field in ("course_id", "faculty_id", "room_id")):
            warnings.append(_issue("section_clash", "existing section cell is replaced by this assignment", line=line,
                                   status=200, offering_id=offering.id))
        state[key], lines[key] = assignment, line
        touched.add(key)
    return _finish(existing, state, touched, lines, violations, warnings)


def rejection(report):
    violations = report["violations"]
    if not violations:
        return None
    # Scope first, then malformed references, then whole-pattern clashes.
    priority = {403: 0, 400: 1, 409: 2}
    return min(violations, key=lambda item: priority.get(item["status"], 3))


def apply_plan(existing, state):
    for key, value in state.items():
        row = existing.get(key)
        if row is None:
            db.session.add(TimetableSlot(**value))
        else:
            for field in FIELDS:
                if getattr(row, field) != value[field]:
                    setattr(row, field, value[field])


def plan_copy(target, source_term_id):
    if TimetableSlot.query.filter_by(offering_id=target.id).first() is not None:
        raise PatternError("offering_not_blank", "target offering already has slots; copy requires a blank offering", 409)
    source = TermOffering.query.filter_by(term_id=source_term_id, section_id=target.section_id).first()
    if source is None:
        raise PatternError("offering_not_found", "source term has no offering for this section", 403)
    existing, state = _snapshot(target.term_id)
    touched = set()
    for row in TimetableSlot.query.filter_by(offering_id=source.id).all():
        key = (target.id, row.day_of_week, row.period_id)
        state[key] = dict((field, getattr(row, field)) for field in FIELDS)
        state[key]["offering_id"] = target.id
        touched.add(key)
    return _finish(existing, state, touched, {}, [], [])


def grouped_slots(offering):
    rows = TimetableSlot.query.filter_by(offering_id=offering.id).all()
    rows.sort(key=lambda row: (row.day_of_week, row.period.start_time, row.id))
    slots, buckets = [], defaultdict(list)
    for row in rows:
        item = dict(row.to_dict(), course=row.course.to_dict(), facultyName=row.faculty.full_name,
                    room=row.room.to_dict(), period=row.period.to_dict())
        slots.append(item)
        buckets[(row.day_of_week, row.session_group_id or f"slot-{row.id}")].append(item)
    groups = []
    for (day, _), members in buckets.items():
        groups.append(dict(dayOfWeek=day, sessionGroupId=members[0]["sessionGroupId"],
                           periodOrdinals=[item["periodOrdinal"] for item in members], slots=members))
    groups.sort(key=lambda item: (item["dayOfWeek"], item["slots"][0]["period"]["startTime"]))
    return dict(offering=offering.to_dict(), slots=slots, groups=groups, count=len(slots))


def colour_token(key):
    """Stable across processes and views; Python's hash() is salted per run."""
    return COLOUR_TOKENS[zlib.crc32(str(key).strip().casefold().encode("utf-8")) % len(COLOUR_TOKENS)]


def _view_warning(code, message, members, severity):
    first = members[0]
    return {"check": "timetable", "error": code, "message": message, "severity": severity,
            "dayOfWeek": first.day_of_week, "periodId": first.period_id, "periodOrdinal": first.period.ordinal,
            "slotIds": [row.id for row in members], "offeringIds": sorted({row.offering_id for row in members})}


def term_warnings(term_id, visible_slot_ids=None):
    """Stored-pattern warnings with slot ids, using the Phase 2.4 rules.

    Teacher/room clashes are refused at import, so stored ones come only from
    raw writes. The unique cell makes a stored section clash impossible; the
    rule is kept so the grid contract matches the import report.
    Optional slot visibility filters the response after whole-term validation.
    """
    rows = TimetableSlot.query.join(TermOffering).filter(TermOffering.term_id == term_id).order_by(TimetableSlot.id).all()
    buckets = {"teacher_clash": defaultdict(list), "room_clash": defaultdict(list), "section_clash": defaultdict(list)}
    for row in rows:
        buckets["teacher_clash"][(row.faculty_id, row.day_of_week, row.period_id)].append(row)
        buckets["room_clash"][(row.room_id, row.day_of_week, row.period_id)].append(row)
        buckets["section_clash"][(row.offering_id, row.day_of_week, row.period_id)].append(row)
    warnings = []
    for code, grouped in buckets.items():
        for key in sorted(grouped):
            members = grouped[key]
            resource_id, day, period_id = key
            offerings = sorted({row.offering_id for row in members})
            if (len(members) if code == "section_clash" else len(offerings)) > 1:
                warnings.append(_view_warning(
                    code, f"{code}: resource {resource_id}, {DAYS[day]}, period {period_id}, offerings {offerings}",
                    members, "WARN" if code == "section_clash" else "HARD"))
    for row in rows:
        if row.course.is_lab != (row.room.type == "LAB"):
            warnings.append(_view_warning(
                "lab_room_mismatch", f"{row.course.code} does not match {row.room.type} room {row.room.name}",
                [row], "WARN"))
    if visible_slot_ids is not None:
        visible = set(visible_slot_ids)
        warnings = [dict(warning, slotIds=[slot_id for slot_id in warning["slotIds"] if slot_id in visible])
                    for warning in warnings if visible.intersection(warning["slotIds"])]
    return warnings


def week_view(view, slots):
    """Five days x six periods; a lab pair is one span-2 block plus a COVERED marker.

    A cell holding several slots (a stored clash) lists the extras in
    ``overlaps`` and is never merged.
    """
    teaching = {row.ordinal: row for row in Period.query.filter(Period.is_break.is_(False)).all()
                if row.ordinal in range(1, 7)}
    break_row = Period.query.filter_by(is_break=True).first()
    grid, courses, sections, rooms = defaultdict(list), {}, {}, {}
    for slot in sorted(slots, key=lambda row: row.id):
        period = slot.period
        if period is None or period.is_break or period.ordinal not in range(1, 7) or slot.day_of_week not in range(5):
            continue
        course, room, section = slot.course, slot.room, slot.offering.section
        label = section.to_dict()["label"]
        entry = {
            "slotId": slot.id, "offeringId": slot.offering_id, "sectionId": section.id, "courseId": course.id,
            "facultyId": slot.faculty_id, "roomId": room.id, "courseCode": course.code, "courseTitle": course.title,
            "isLab": course.is_lab, "roomName": room.name, "facultyName": slot.faculty.full_name,
            "sectionLabel": label, "sessionGroupId": slot.session_group_id, "colour": colour_token(course.code),
        }
        entry.pop(VIEW_OMITS[view])
        grid[(slot.day_of_week, period.ordinal)].append(entry)
        courses[course.id] = {"id": course.id, "code": course.code, "title": course.title,
                              "isLab": course.is_lab, "colour": colour_token(course.code)}
        sections[section.id] = {"id": section.id, "label": label, "colour": colour_token(label)}
        rooms[room.id] = {"id": room.id, "name": room.name, "type": room.type, "colour": colour_token(room.name)}
    days = []
    for day in range(5):
        cells, ordinal = [], 1
        while ordinal <= 6:
            entries, period = grid.get((day, ordinal), []), teaching.get(ordinal)
            base = {"dayOfWeek": day, "period": ordinal, "periodId": period.id if period else None}
            if not entries:
                cells.append(dict(base, kind="EMPTY", span=1))
                ordinal += 1
                continue
            following, after = grid.get((day, ordinal + 1), []), teaching.get(ordinal + 1)
            group = entries[0]["sessionGroupId"]
            # Same adjacency rule as pairing: never merge across the break.
            merged = (group is not None and len(entries) == 1 and len(following) == 1
                      and following[0]["sessionGroupId"] == group and period is not None
                      and after is not None and period.end_time == after.start_time)
            span = 2 if merged else 1
            block = entries[:1] + (following if merged else [])
            cells.append(dict(entries[0], **base, kind="SLOT", span=span, periods=list(range(ordinal, ordinal + span)),
                              slotIds=[item["slotId"] for item in block], overlaps=entries[1:]))
            if merged:
                cells.append({"dayOfWeek": day, "period": ordinal + 1, "periodId": after.id,
                              "kind": "COVERED", "span": 0, "coveredBy": ordinal})
            ordinal += span
        days.append({"dayOfWeek": day, "label": DAYS[day], "cells": cells})
    legend = {
        "courses": sorted(courses.values(), key=lambda item: (item["code"].casefold(), item["id"])),
        "sections": sorted(sections.values(), key=lambda item: (item["label"], item["id"])),
        "rooms": sorted(rooms.values(), key=lambda item: (item["name"].casefold(), item["id"])),
    }
    return {"periods": [teaching[key].to_dict() for key in sorted(teaching)],
            "break": break_row.to_dict() if break_row else None, "days": days, "legend": legend}
