r"""Read-only inventory of legacy slotless occurrences for Phase 2.9.

Lists every ``ScheduledClass`` row with no slot, its active mapping (if any)
and the term's offerings, so an operator can prepare an authoritative mapping
file. Strictly read-only: the app is built with ``initialise_database=False``,
so not even the app's idempotent startup (``create_all`` + ``seed_periods``)
runs, and the session is rolled back. This script issues no DDL and no INSERT,
UPDATE or DELETE.

Prerequisites (both must already be true; this script will not create them):
    - the base schema exists in the target database;
    - backend/migrate_2_9.sql has been applied to it.

Run from backend/:

    .\venv\Scripts\python.exe legacy_mapping_inventory.py --term-id 7 \
        --date-from 2025-01-01 --date-to 2025-12-31 --output inventory.csv

Columns: occurrence_id, start_time_utc, course_code, professor_id,
professor_email, room, term_id, status, offering_id, section_id, section,
department, mapped_by, mapped_at, reason.

An operator's mapping file has exactly four columns:

    occurrence_id,offering_id,mapped_by,reason

Then validate it with import_legacy_mappings.py --file <file> (dry run) and
only then add --apply.
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, timezone

os.environ["SCHEDULER_ENABLED"] = "false"

from sqlalchemy import and_, or_  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    LegacyOccurrenceMapping, ScheduledClass, Section, Term, TermOffering, User,
)
from app.services import legacy_mapping, occurrences  # noqa: E402

COLUMNS = ["occurrence_id", "start_time_utc", "course_code", "professor_id", "professor_email",
           "room", "term_id", "status", "offering_id", "section_id", "section", "department",
           "mapped_by", "mapped_at", "reason"]


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from None


def rows_for(term_id, first, last):
    start, end = occurrences.utc_bounds(first, last)
    professor = aliased(User)
    query = db.session.query(ScheduledClass, LegacyOccurrenceMapping, TermOffering, Section, professor).outerjoin(
        LegacyOccurrenceMapping,
        and_(LegacyOccurrenceMapping.occurrence_id == ScheduledClass.id,
             LegacyOccurrenceMapping.superseded_at.is_(None)),
    ).outerjoin(TermOffering, TermOffering.id == LegacyOccurrenceMapping.offering_id).outerjoin(
        Section, Section.id == TermOffering.section_id,
    ).outerjoin(professor, professor.id == ScheduledClass.professor_id).filter(
        ScheduledClass.slot_id.is_(None),
        ScheduledClass.start_time >= start,
        ScheduledClass.start_time < end,
    )
    if term_id is not None:
        # A slotless row without a term is in scope for every term covering it.
        query = query.filter(or_(ScheduledClass.term_id == term_id, ScheduledClass.term_id.is_(None)))
    return query.order_by(ScheduledClass.start_time, ScheduledClass.id).all()


def record(row, mapping, offering, section, professor) -> dict:
    return {
        "occurrence_id": row.id,
        "start_time_utc": row.start_time.replace(tzinfo=timezone.utc).isoformat(),
        "course_code": row.course_code,
        "professor_id": row.professor_id,
        "professor_email": professor.email if professor else "",
        "room": row.room or "",
        "term_id": row.term_id if row.term_id is not None else "",
        "status": "MAPPED" if mapping is not None else "UNMAPPED",
        "offering_id": offering.id if offering else "",
        "section_id": section.id if section else "",
        "section": section.to_dict()["label"] if section else "",
        "department": section.department if section else "",
        "mapped_by": mapping.mapped_by if mapping else "",
        "mapped_at": mapping.mapped_at.replace(tzinfo=timezone.utc).isoformat() if mapping else "",
        "reason": mapping.reason if mapping else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--term-id", type=int, help="limit to one term (its blank offerings are listed)")
    parser.add_argument("--date-from", required=True, type=parse_day)
    parser.add_argument("--date-to", required=True, type=parse_day)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--output", help="write to this file instead of stdout")
    parser.add_argument("--offerings", action="store_true",
                        help="also list the term's offerings as the only valid offering_id values")
    args = parser.parse_args()
    if args.date_to < args.date_from:
        print("Refused: --date-to precedes --date-from.")
        return 1

    app = create_app("development", initialise_database=False)
    with app.app_context():
        try:
            if "legacy_occurrence_mappings" not in db.inspect(db.engine).get_table_names():
                print("Refused: legacy_occurrence_mappings is missing. This tool never creates "
                      "schema; apply backend/migrate_2_9.sql to this database first.")
                return 1
            if args.term_id is not None and db.session.get(Term, args.term_id) is None:
                print(f"Refused: term {args.term_id} does not exist.")
                return 1
            records = [record(*values) for values in rows_for(args.term_id, args.date_from, args.date_to)]
            unmapped = sum(1 for item in records if item["status"] == "UNMAPPED")
            offerings = []
            if args.offerings:
                query = db.session.query(TermOffering, Section).join(Section).filter(
                    TermOffering.term_id == args.term_id) if args.term_id is not None else None
                if query is None:
                    print("Refused: --offerings needs --term-id.")
                    return 1
                offerings = [{"offering_id": offering.id, "section_id": section.id,
                              "section": section.to_dict()["label"], "department": section.department}
                             for offering, section in query.order_by(TermOffering.id).all()]
            if args.format == "json":
                text = json.dumps({"occurrences": records, "offerings": offerings}, indent=2)
            else:
                buffer = io.StringIO(newline="")
                writer = csv.writer(buffer)
                writer.writerow(COLUMNS)
                writer.writerows([[item[key] for key in COLUMNS] for item in records])
                if offerings:
                    writer.writerow([])
                    writer.writerow(["# offerings: offering_id, section_id, section, department"])
                    writer.writerow(["offering_id", "section_id", "section", "department"])
                    writer.writerows([[item["offering_id"], item["section_id"], item["section"],
                                       item["department"]] for item in offerings])
                text = buffer.getvalue().rstrip("\n")
            if args.output:
                with open(args.output, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text + "\n")
                print(f"{len(records)} slotless occurrence(s), {unmapped} unmapped; wrote {args.output}")
            else:
                print(text)
                print(f"\n{len(records)} slotless occurrence(s), {unmapped} unmapped.", file=sys.stderr)
            if unmapped:
                print("Legacy compatibility is NOT complete until every row above is mapped.", file=sys.stderr)
            backlog = legacy_mapping.unmapped_total()
            print(f"Global unmapped backlog: {backlog}. HOD attendance reports stay disabled "
                  "while it is above zero (the refusal is identical for every range).", file=sys.stderr)
            return 0
        finally:
            # Read-only intent: never leave a write pending.
            db.session.rollback()


if __name__ == "__main__":
    sys.exit(main())
