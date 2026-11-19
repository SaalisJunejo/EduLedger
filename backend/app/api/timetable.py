"""Timetable endpoints.

- POST /api/v1/hod/timetable/upload - HOD-only ingestion of dated class
  occurrences from a multipart CSV or XLSX file.
- GET  /api/v1/timetable/classes    - HOD/ADMIN read-only listing of every
  scheduled occurrence (the institution-wide counterpart of the faculty's
  own GET /api/v1/faculty/classes).
- Phase 2.2: authenticated periods, ADMIN terms/activation, scoped master
  CRUD and HOD offerings. Copy-from awaits TimetableSlot in Phase 2.4.
"""

import csv
import io
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from flask import current_app, g, jsonify, request
from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db
from ..models import Course, DirectoryEntry, Period, Room, ScheduledClass, Section, Term, TermOffering, User, UserRole
from ..models.timetable import ROOM_TYPES
from ..services.auth import require_auth
from ..services.directory import apply_import, plan_import
from ..services.timetable import COLUMNS, activate_term, timetable_timezone, validate_row
from ..util import utcnow
from . import api_v1
from .errors import api_error

_MAX_BYTES = 10 * 1024 * 1024
_MAX_ROWS = 5000
_MAX_XLSX_EXPANDED_BYTES = 50 * 1024 * 1024
# Cap on the read-only listing below (newest occurrences first).
_MAX_LISTED_CLASSES = 500


def _read_table(data: bytes, suffix: str) -> tuple[list[str], list[tuple[int, list]]]:
    """Read before creating any rows; reject malformed/oversized input atomically."""
    workbook = None
    try:
        if suffix == ".csv":
            rows = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        else:
            with ZipFile(io.BytesIO(data)) as archive:
                if sum(member.file_size for member in archive.infolist()) > _MAX_XLSX_EXPANDED_BYTES:
                    raise ValueError("expanded XLSX exceeds 50 MB")
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
            rows = workbook.active.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            raise ValueError("file is empty; column headers are required")
        headers = [str(value).strip() if value is not None else "" for value in header]
        if any(not column for column in headers) or len(set(headers)) != len(headers):
            raise ValueError("column headers must be nonempty and unique")
        missing = sorted(set(COLUMNS) - set(headers))
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")
        records = []
        for number, row in enumerate(rows, start=2):
            if number > _MAX_ROWS + 1:
                raise ValueError(f"file exceeds {_MAX_ROWS} data rows")
            records.append((number, list(row)))
        return headers, records
    finally:
        if workbook is not None:
            workbook.close()


@api_v1.post("/hod/timetable/upload")
@require_auth(roles=["HOD"])
def upload_timetable():
    """Create valid rows and report spreadsheet row numbers/reasons for bad rows.

    Field: file. Required columns: course_code, faculty_employee_id, room,
    day_of_week (Monday-Sunday or 0-6), start_time, end_time (HH:MM[:SS]).
    Faculty department restriction is intentionally not enforced pending policy.
    Every upload creates dated occurrences; it is not an upsert/weekly series.
    """
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return api_error("timetable", "missing_file", "multipart file field is required", 400)
    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        return api_error("timetable", "unsupported_file_type", "file must be .csv or .xlsx", 400)
    data = uploaded.stream.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        return api_error("timetable", "file_too_large", "file must be at most 10 MB", 413)
    try:
        zone = timetable_timezone()
    except ValueError as exc:
        return api_error("timetable", "invalid_configuration", str(exc), 500)
    try:
        headers, records = _read_table(data, suffix)
    except Exception as exc:
        return api_error("timetable", "invalid_file", f"cannot parse timetable: {exc}", 400)

    created = []
    rejected = []
    now = utcnow()
    try:
        for number, values in records:
            if len(values) != len(headers):
                rejected.append({"row": number, "reasons": ["row has a different number of cells than the header"]})
                continue
            scheduled_class, reasons = validate_row(dict(zip(headers, values)), zone, now)
            if reasons:
                rejected.append({"row": number, "reasons": reasons})
            else:
                db.session.add(scheduled_class)
                created.append(scheduled_class)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Timetable upload database failure")
        return api_error("timetable", "database_error", "timetable could not be saved; no rows were created", 500)
    return jsonify({
        "rows_created": len(created), "rows_rejected": rejected,
        "classes": [scheduled_class.to_dict() for scheduled_class in created],
        "timezone": current_app.config["TIMETABLE_TIMEZONE"],
    }), 201 if created else 200


@api_v1.get("/timetable/classes")
@require_auth(roles=["HOD", "ADMIN"])
def list_timetable_classes():
    """Every scheduled occurrence, newest start time first (at most 500).

    Query: optional ``professor_id`` to limit the list to one professor's
    occurrences. Each entry carries the scheduled professor's name so the
    HOD/Admin surfaces can offer a faculty filter without a second call.
    Faculty department restriction is intentionally not enforced pending
    policy, the same as the upload above.
    """
    professor_id = None
    raw_professor_id = (request.args.get("professor_id") or "").strip()
    if raw_professor_id:
        if not (
            raw_professor_id.isascii()
            and raw_professor_id.isdigit()
            and len(raw_professor_id) <= 10
            and 0 < int(raw_professor_id) <= 2147483647
        ):
            return api_error(
                "request", "invalid_professor_id", "professor_id must be a positive user id", 400
            )
        professor_id = int(raw_professor_id)

    query = db.session.query(ScheduledClass, User).outerjoin(
        User, User.id == ScheduledClass.professor_id
    )
    if professor_id is not None:
        query = query.filter(ScheduledClass.professor_id == professor_id)
    rows = query.order_by(
        ScheduledClass.start_time.desc(), ScheduledClass.id.desc(),
    ).limit(_MAX_LISTED_CLASSES).all()

    fields = ("id", "courseCode", "room", "professorId", "dayOfWeek", "startTime", "endTime")
    entries = []
    for scheduled_class, professor in rows:
        serialized = scheduled_class.to_dict()
        entry = {field: serialized[field] for field in fields}
        entry["professorName"] = professor.full_name if professor is not None else None
        entries.append(entry)
    return jsonify({"classes": entries, "count": len(entries), "limit": _MAX_LISTED_CLASSES})


# Phase 2.2 master data. JSON inputs use snake_case; models serialize camelCase.
_MASTER_FIELDS = {
    Section: {"programme", "batch_year", "letter", "department"},
    Course: {"code", "title", "department", "is_lab"},
    Room: {"name", "type", "block", "capacity", "department"},
}


def _master_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("a JSON object is required")
    return body


def _master_text(body, field, limit):
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


def _master_department():
    """HOD scope comes only from the authenticated user record, never the body."""
    if g.role == "ADMIN":
        return None, None
    user = db.session.get(User, g.user_id)
    if user is None or not user.is_active or user.role != UserRole.HOD or not user.department:
        return None, api_error("timetable", "out_of_department", "HOD has no valid department scope", 403)
    return user.department, None


def _master_scope_error(department, target):
    if department is not None and target != department:
        return api_error("timetable", "out_of_department", "HOD may act only within their own department", 403)
    return None


def _master_values(model, body, existing, department):
    if set(body) - _MASTER_FIELDS[model]:
        raise ValueError("unknown fields: " + ", ".join(sorted(set(body) - _MASTER_FIELDS[model])))
    values = {field: getattr(existing, field) for field in _MASTER_FIELDS[model]} if existing else {}
    values.update(body)
    if "department" not in values and department is not None:
        values["department"] = department
    values["department"] = _master_text(values, "department", 128)
    fields = {
        Section: (("programme", 64), ("letter", 4)),
        Course: (("code", 32), ("title", 128)),
        Room: (("name", 64), ("type", 16)),
    }
    for field, limit in fields[model]:
        values[field] = _master_text(values, field, limit)
    if model is Section:
        year = values.get("batch_year")
        if type(year) is not int or not 1 <= year <= 9999:
            raise ValueError("batch_year must be an integer year between 1 and 9999")
    elif model is Course:
        values.setdefault("is_lab", False)
        if type(values["is_lab"]) is not bool:
            raise ValueError("is_lab must be a boolean")
    else:
        if values["type"] not in ROOM_TYPES:
            raise ValueError("type must be LECTURE or LAB")
        block = values.setdefault("block", None)
        if block is not None:
            values["block"] = _master_text(values, "block", 64)
        capacity = values.setdefault("capacity", None)
        if capacity is not None and (type(capacity) is not int or not 0 < capacity <= 2147483647):
            raise ValueError("capacity must be null or a positive integer")
    return values


def _master_database_error(exc):
    db.session.rollback()
    if isinstance(exc, IntegrityError):
        return api_error("timetable", "master_data_conflict", "duplicate data or a resource still in use", 409)
    current_app.logger.exception("Timetable master data database failure")
    return api_error("timetable", "database_error", "master data could not be saved", 500)


def _master_collection(model, key):
    department, error = _master_department()
    if error is not None:
        return error
    if request.method in {"GET", "HEAD"}:
        query = model.query
        if department is not None:
            query = query.filter(model.department == department)
        rows = query.order_by(model.id).all()
        return jsonify({key: [row.to_dict() for row in rows], "count": len(rows)})
    try:
        body = _master_body()
        error = _master_scope_error(department, body.get("department", department))
        if error is not None:
            return error
        row = model(**_master_values(model, body, None, department))
        db.session.add(row)
        db.session.commit()
    except ValueError as exc:
        return api_error("timetable", "invalid_fields", str(exc), 400)
    except SQLAlchemyError as exc:
        return _master_database_error(exc)
    return jsonify({key[:-1]: row.to_dict()}), 201


def _master_detail(model, key, resource_id):
    department, error = _master_department()
    if error is not None:
        return error
    row = db.session.get(model, resource_id)
    if row is None:
        code = {Course: "course_unknown", Room: "room_unknown", Section: "section_not_found"}[model]
        return api_error("timetable", code, f"{key} does not exist", 404 if model is Section else 400)
    error = _master_scope_error(department, row.department)
    if error is not None:
        return error
    if request.method in {"GET", "HEAD"}:
        return jsonify({key: row.to_dict()})
    try:
        if request.method == "DELETE":
            # Sections are permanent cohorts: never discard an offering's history.
            if model is Section and TermOffering.query.filter_by(section_id=row.id).first() is not None:
                return api_error("timetable", "master_data_conflict", "section has term offerings and cannot be deleted", 409)
            db.session.delete(row)
            db.session.commit()
            return jsonify({"deleted": resource_id})
        body = _master_body()
        error = _master_scope_error(department, body.get("department", row.department))
        if error is not None:
            return error
        values = _master_values(model, body, row, department)
        for field, value in values.items():
            setattr(row, field, value)
        db.session.commit()
    except ValueError as exc:
        return api_error("timetable", "invalid_fields", str(exc), 400)
    except SQLAlchemyError as exc:
        return _master_database_error(exc)
    return jsonify({key: row.to_dict()})


@api_v1.get("/periods")
@require_auth(roles=["ADMIN", "HOD", "FACULTY", "STUDENT"])
def timetable_periods():
    rows = Period.query.order_by(Period.start_time, Period.id).all()
    return jsonify({"periods": [row.to_dict() for row in rows], "count": len(rows)})


@api_v1.route("/admin/terms", methods=["GET", "POST"])
@require_auth(roles=["ADMIN"])
def timetable_terms():
    if request.method in {"GET", "HEAD"}:
        rows = Term.query.order_by(Term.start_date.desc(), Term.id.desc()).all()
        return jsonify({"terms": [row.to_dict() for row in rows], "count": len(rows)})
    try:
        body = _master_body()
        if set(body) - {"name", "start_date", "end_date", "is_active"}:
            raise ValueError("only name, start_date, end_date and is_active are accepted")
        name = _master_text(body, "name", 64)
        dates = []
        for field in ("start_date", "end_date"):
            raw = _master_text(body, field, 10)
            parsed = date.fromisoformat(raw)
            if parsed.isoformat() != raw:
                raise ValueError(f"{field} must use YYYY-MM-DD")
            dates.append(parsed)
        if dates[1] < dates[0]:
            raise ValueError("end_date must be on or after start_date")
        active = body.get("is_active", False)
        if type(active) is not bool:
            raise ValueError("is_active must be a boolean")
        term = Term(name=name, start_date=dates[0], end_date=dates[1], is_active=False)
        db.session.add(term)
        db.session.flush()
        created, deactivated = activate_term(term) if active else (0, [])
        db.session.commit()
    except ValueError as exc:
        return api_error("timetable", "invalid_fields", str(exc), 400)
    except SQLAlchemyError as exc:
        return _master_database_error(exc)
    return jsonify({"term": term.to_dict(), "offerings_created": created, "deactivated_term_ids": deactivated}), 201


@api_v1.post("/admin/terms/<int:term_id>/activate")
@require_auth(roles=["ADMIN"])
def timetable_activate_term(term_id):
    term = db.session.get(Term, term_id)
    if term is None:
        return api_error("timetable", "term_not_found", "term does not exist", 404)
    try:
        created, deactivated = activate_term(term)
        db.session.commit()
    except SQLAlchemyError as exc:
        return _master_database_error(exc)
    return jsonify({"term": term.to_dict(), "offerings_created": created, "deactivated_term_ids": deactivated})


@api_v1.route("/sections", methods=["GET", "POST"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_sections():
    return _master_collection(Section, "sections")


@api_v1.route("/sections/<int:resource_id>", methods=["GET", "PATCH", "PUT", "DELETE"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_section(resource_id):
    return _master_detail(Section, "section", resource_id)


@api_v1.route("/courses", methods=["GET", "POST"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_courses():
    return _master_collection(Course, "courses")


@api_v1.route("/courses/<int:resource_id>", methods=["GET", "PATCH", "PUT", "DELETE"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_course(resource_id):
    return _master_detail(Course, "course", resource_id)


@api_v1.route("/rooms", methods=["GET", "POST"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_rooms():
    return _master_collection(Room, "rooms")


@api_v1.route("/rooms/<int:resource_id>", methods=["GET", "PATCH", "PUT", "DELETE"])
@require_auth(roles=["HOD", "ADMIN"])
def timetable_room(resource_id):
    return _master_detail(Room, "room", resource_id)


@api_v1.get("/hod/offerings")
@require_auth(roles=["HOD"])
def timetable_offerings():
    department, error = _master_department()
    if error is not None:
        return error
    query = TermOffering.query.join(Section).filter(Section.department == department)
    raw_term = request.args.get("term_id")
    if raw_term is not None:
        if len(raw_term) > 10 or not raw_term.isascii() or not raw_term.isdigit() or not 0 < int(raw_term) <= 2147483647:
            return api_error("timetable", "invalid_fields", "term_id must be a positive integer", 400)
        query = query.filter(TermOffering.term_id == int(raw_term))
    rows = query.order_by(TermOffering.term_id, TermOffering.section_id).all()
    return jsonify({"offerings": [dict(row.to_dict(), section=row.section.to_dict()) for row in rows], "count": len(rows)})


@api_v1.post("/admin/directory/import")
@require_auth(roles=["ADMIN"])
def timetable_directory_import():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return api_error("directory", "missing_file", "multipart file field is required", 400)
    if Path(uploaded.filename).suffix.lower() != ".csv":
        return api_error("directory", "unsupported_file_type", "file must be .csv", 400)
    raw_dry_run = request.args.get("dry_run", request.form.get("dry_run", "false")).strip().lower()
    if raw_dry_run not in {"true", "false"}:
        return api_error("directory", "invalid_fields", "dry_run must be true or false", 400)
    dry_run = raw_dry_run == "true"
    data = uploaded.stream.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        return api_error("directory", "file_too_large", "file must be at most 10 MB", 413)
    try:
        report, records, new_sections, existing = plan_import(data, lock=not dry_run)
        if report["rows_rejected"]:
            db.session.rollback()
            return api_error("directory", "invalid_file", "directory rejected; no rows written", 400,
                             dry_run=dry_run, **report)
        if dry_run:
            db.session.rollback()
        else:
            apply_import(records, new_sections, existing)
            db.session.commit()
    except (ValueError, UnicodeError, csv.Error) as exc:
        db.session.rollback()
        return api_error("directory", "invalid_file", str(exc), 400,
                         rows_rejected=[{"line": 1, "row": 1, "reasons": [str(exc)]}])
    except IntegrityError:
        db.session.rollback()
        return api_error("directory", "master_data_conflict", "concurrent directory/section conflict; no rows written", 409)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Directory import database failure")
        return api_error("directory", "database_error", "directory could not be saved; no rows written", 500)
    return jsonify(dict(report, dry_run=dry_run)), 200


@api_v1.get("/admin/directory")
@require_auth(roles=["ADMIN"])
def timetable_directory_list():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "50"))
        if page < 1 or page > 2147483647 or not 1 <= per_page <= 100:
            raise ValueError
    except ValueError:
        return api_error("directory", "invalid_fields", "page must be positive; per_page must be 1-100", 400)
    query = DirectoryEntry.query
    search = request.args.get("search", "").strip()
    if search:
        # Treat search as text, not as user-supplied SQL wildcard syntax.
        query = query.filter(db.or_(
            DirectoryEntry.roll_number.icontains(search, autoescape=True),
            DirectoryEntry.full_name.icontains(search, autoescape=True),
            DirectoryEntry.university_email.icontains(search, autoescape=True),
        ))
    total = query.count()
    rows = query.order_by(DirectoryEntry.roll_number, DirectoryEntry.id).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({"entries": [row.to_dict() for row in rows], "total": total,
                    "page": page, "per_page": per_page})
