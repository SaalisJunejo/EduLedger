"""Monthly attendance export (Phase 1.3, requirements doc section 3.5).

- GET /api/v1/attendance/export?class_id=<ScheduledClass.id>
  &professor_id=<User.id>&month=YYYY-MM&format=xlsx|csv - the month's
  check-ins as a downloadable file.

FACULTY may export only classes they are the scheduled professor of
(omit class_id for all of them); HOD and ADMIN may export any class (omit
class_id for every class, or pass professor_id for one faculty member's
classes). The query is read-only and touches the local mirror only - see
app/services/attendance_export.py.
"""

import io

from flask import g, request, send_file

from ..extensions import db
from ..models import ScheduledClass, User
from ..services import attendance_export
from ..services.auth import require_auth
from ..services.timetable import timetable_timezone
from . import api_v1
from .errors import api_error


@api_v1.get("/attendance/export")
@require_auth(roles=["FACULTY", "HOD", "ADMIN"])
def export_attendance():
    """Download one month of attendance as xlsx (default) or csv.

    Query: ``month=YYYY-MM`` (required; a calendar month in
    TIMETABLE_TIMEZONE), ``format=xlsx|csv``, optional ``class_id`` and
    optional ``professor_id`` (that faculty member's classes only; FACULTY
    may pass nothing but their own id). The xlsx carries a second sheet
    "Summary" (lectures attended and courses covered per student); the csv
    is the attendance table only. A month without check-ins still returns
    a file with the header row(s).
    """
    fmt = (request.args.get("format") or "xlsx").strip().lower()
    if fmt not in attendance_export.FORMATS:
        return api_error("request", "invalid_format", "format must be xlsx or csv")

    try:
        zone = timetable_timezone()
    except ValueError as exc:
        return api_error("export", "invalid_configuration", str(exc), 500)

    month = (request.args.get("month") or "").strip()
    try:
        start_utc, end_utc = attendance_export.month_range_utc(month, zone)
    except ValueError as exc:
        return api_error("request", "invalid_month", str(exc))

    class_id = None
    scheduled_class = None
    raw_class_id = (request.args.get("class_id") or "").strip()
    if raw_class_id:
        if not (
            raw_class_id.isascii()
            and raw_class_id.isdigit()
            and len(raw_class_id) <= 10
            and 0 < int(raw_class_id) <= 2147483647
        ):
            return api_error(
                "request", "invalid_class_id", "class_id must be a positive ScheduledClass.id"
            )
        class_id = int(raw_class_id)
        scheduled_class = db.session.get(ScheduledClass, class_id)
        if scheduled_class is None:
            return api_error(
                "class", "class_not_found", f"scheduled class {class_id} does not exist", 404
            )
        if g.role == "FACULTY" and scheduled_class.professor_id != g.user_id:
            return api_error(
                "professor",
                "not_class_professor",
                f"class {class_id} is not scheduled for the authenticated faculty",
                403,
            )

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
                "request", "invalid_professor_id", "professor_id must be a positive user id"
            )
        professor_id = int(raw_professor_id)
        # Faculty never widen their own scope, so the only id they may name
        # is their own (which is what they get anyway).
        if g.role == "FACULTY" and professor_id != g.user_id:
            return api_error(
                "professor",
                "not_class_professor",
                "faculty may export only their own classes",
                403,
            )
        if db.session.get(User, professor_id) is None:
            return api_error(
                "request", "invalid_professor_id", f"user {professor_id} does not exist"
            )
        if scheduled_class is not None and scheduled_class.professor_id != professor_id:
            return api_error(
                "request",
                "class_professor_mismatch",
                f"class {class_id} is not scheduled for professor {professor_id}",
            )
    if g.role == "FACULTY":
        professor_id = g.user_id

    rows, summary = attendance_export.build_report(
        start_utc,
        end_utc,
        zone,
        class_id=class_id,
        professor_id=professor_id,
    )
    if fmt == "xlsx":
        content = attendance_export.build_xlsx(rows, summary)
    else:
        content = attendance_export.build_csv(rows)

    response = send_file(
        io.BytesIO(content),
        mimetype=attendance_export.FORMATS[fmt],
        as_attachment=True,
        download_name=attendance_export.filename(month, fmt),
    )
    response.headers["Cache-Control"] = "no-store"
    return response
