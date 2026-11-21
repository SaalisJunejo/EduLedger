"""Phase 2.9 read-only attendance portal, downloads and reconciliation."""

from flask import g, jsonify, request, send_file
import io

from ..extensions import db
from ..models import ScheduledClass
from ..services import attendance_portal as reports
from ..services import blockchain, legacy_mapping
from ..services.auth import require_auth
from . import api_v1
from .errors import api_error

# The service returns the required out_of_scope envelope for authenticated
# ADMIN/STUDENT callers too; only FACULTY and own-department HOD are authorized.
AUTH_ROLES = ["FACULTY", "HOD", "ADMIN", "STUDENT"]


def _read(matrix=False, sessions=False, verify=False, occurrence_id=None):
    try:
        actor = reports.actor_for(g.user_id, g.role)
        # HOD reports are disabled while ANY legacy row is unmapped. Taken
        # before the occurrence row is read: a 403-on-unmapped / 409-on-mapped
        # split would let an HOD probe individual occurrence ids.
        reports.refuse_hod_reports(actor)
        args = request.args.to_dict()
        mapping = None
        if occurrence_id is not None:
            row = db.session.get(ScheduledClass, occurrence_id)
            if row is not None and row.slot_id is None:
                # The only section source for a slotless occurrence.
                mapping = legacy_mapping.active_mapping(occurrence_id)
            if row is None:
                raise reports.ReportError("out_of_scope", "occurrence is outside your attendance scope", 403)
            if g.role == "FACULTY" and row.professor_id != actor.id:
                raise reports.ReportError("out_of_scope", "occurrence is outside your attendance scope", 403)
            if g.role == "HOD":
                if row.slot is not None:
                    section = row.slot.offering.section
                elif mapping is not None:
                    section = mapping.offering.section
                else:
                    # An unmapped slotless row has no department to compare.
                    raise reports.ReportError("out_of_scope", "occurrence is outside your attendance scope", 403)
                if section.department != actor.department:
                    raise reports.ReportError("out_of_scope", "occurrence is outside your attendance scope", 403)
            if row.slot is not None:
                args.setdefault("term", str(row.term_id))
                args.setdefault("section", str(row.slot.offering.section_id))
            elif mapping is not None:
                args.setdefault("term", str(mapping.offering.term_id))
                args.setdefault("section", str(mapping.offering.section_id))
        fmt = str(args.get("format", "json")).lower()
        if fmt not in {"json", "csv", "xlsx"}:
            raise reports.ReportError("invalid_format", "format must be json, csv or xlsx")
        # Downloads deliberately ignore paging, even malformed offset/limit.
        downloading = not (matrix or sessions or verify or occurrence_id is not None) and fmt != "json"
        offset = reports.integer(args.get("offset"), "offset", 0, minimum=0) if not downloading else 0
        limit = reports.integer(args.get("limit"), "limit", 50, maximum=500) if not downloading else 50
        if occurrence_id is not None and row.slot is None and mapping is None:
            return jsonify(reports.legacy_roster_payload(actor, row, args))
        term, filters = reports.filters_for(args)
        report = reports.build_report(actor, term, filters, roster=occurrence_id is not None)
        if verify:
            return jsonify(reports.verify_report(report, offset, limit))
        if occurrence_id is not None:
            return jsonify(reports.roster_payload(report, occurrence_id))
        if sessions:
            return jsonify(dict(report["metadata"], rows=[reports.session_payload(m) for m in report["sessions"]],
                                total=len(report["sessions"])))
        if downloading:
            return send_file(io.BytesIO(reports.download(report, fmt)), mimetype=reports.MIMES[fmt],
                             as_attachment=True, download_name=f"eduledger-attendance-term-{term.id}.{fmt}")
        return jsonify(reports.page_report(report, offset, limit, matrix=matrix))
    except reports.ReportError as exc:
        return api_error("attendance", exc.error, exc.message, exc.status)
    except blockchain.BlockchainError as exc:
        return api_error("blockchain", exc.code, exc.message, 502)


@api_v1.get("/faculty/attendance")
@require_auth(roles=AUTH_ROLES)
def faculty_attendance_report():
    return _read()


@api_v1.get("/faculty/attendance/matrix")
@require_auth(roles=AUTH_ROLES)
def faculty_attendance_matrix():
    return _read(matrix=True)


@api_v1.get("/faculty/sessions")
@require_auth(roles=AUTH_ROLES)
def faculty_attendance_sessions():
    return _read(sessions=True)


@api_v1.get("/faculty/sessions/<int:occurrence_id>/roster")
@require_auth(roles=AUTH_ROLES)
def faculty_attendance_roster(occurrence_id):
    return _read(occurrence_id=occurrence_id)


@api_v1.get("/timetable/verify")
@require_auth(roles=AUTH_ROLES)
def timetable_verify_attendance():
    return _read(verify=True)
