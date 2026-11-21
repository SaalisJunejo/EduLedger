r"""Apply an operator's authoritative legacy occurrence mapping file.

Phase 2.9 never infers a section for a slotless occurrence. An operator states
it here, with a reason; nothing is backfilled and no course, room, time,
professor or captured student is used to guess.

Mapping file (exactly these four columns):

    occurrence_id,offering_id,mapped_by,reason

Run from backend/:

    .\venv\Scripts\python.exe import_legacy_mappings.py --file mappings.csv
    .\venv\Scripts\python.exe import_legacy_mappings.py --file mappings.csv --apply

Without --apply every row is validated in a real transaction (the service and
the database triggers both run) and then rolled back. A file is applied
atomically: one bad row writes nothing. An occurrence that already has an
active mapping is replaced through the service's audited supersede, never by
editing the old row.

The app is built with ``initialise_database=False``: this tool never creates
schema and never seeds periods, so the base schema and backend/migrate_2_9.sql
must already be applied to the target database.
"""

import argparse
import csv
import os
import sys

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import legacy_mapping  # noqa: E402

COLUMNS = ["occurrence_id", "offering_id", "mapped_by", "reason"]


def read_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or [name.strip() for name in reader.fieldnames] != COLUMNS:
            raise ValueError(f"header must be exactly {','.join(COLUMNS)}")
        rows = []
        for line, raw in enumerate(reader, start=2):
            if not any((value or "").strip() for value in raw.values()):
                continue  # blank line
            try:
                occurrence_id, offering_id = int(raw["occurrence_id"]), int(raw["offering_id"])
            except (TypeError, ValueError):
                raise ValueError(f"line {line}: occurrence_id and offering_id must be integers") from None
            if occurrence_id < 1 or offering_id < 1:
                raise ValueError(f"line {line}: ids must be positive")
            rows.append({"line": line, "occurrence_id": occurrence_id, "offering_id": offering_id,
                         "mapped_by": (raw["mapped_by"] or "").strip(),
                         "reason": (raw["reason"] or "").strip()})
    if not rows:
        raise ValueError("the file has no mapping rows")
    seen = set()
    for row in rows:
        if row["occurrence_id"] in seen:
            raise ValueError(f"line {row['line']}: occurrence {row['occurrence_id']} appears twice")
        seen.add(row["occurrence_id"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="mapping CSV (four columns)")
    parser.add_argument("--apply", action="store_true", help="commit; without it the file is only validated")
    args = parser.parse_args()
    if os.getenv("FLASK_CONFIG", "development") != "development":
        print("Refused: FLASK_CONFIG must be development.")
        return 1
    try:
        rows = read_rows(args.file)
    except (OSError, ValueError) as exc:
        print(f"Refused: {exc}")
        return 1

    app = create_app("development", initialise_database=False)
    with app.app_context():
        results = []
        try:
            if "legacy_occurrence_mappings" not in db.inspect(db.engine).get_table_names():
                print("Refused: legacy_occurrence_mappings is missing. Apply "
                      "backend/migrate_2_9.sql to this database first.")
                return 1
            for row in rows:
                action = "superseded" if legacy_mapping.active_mapping(row["occurrence_id"]) else "mapped"
                try:
                    if action == "superseded":
                        entry = legacy_mapping.supersede(row["occurrence_id"], row["offering_id"],
                                                         row["mapped_by"], row["reason"])
                    else:
                        entry = legacy_mapping.map_occurrence(row["occurrence_id"], row["offering_id"],
                                                              row["mapped_by"], row["reason"])
                except legacy_mapping.MappingError as exc:
                    raise ValueError(
                        f"line {row['line']} occurrence {row['occurrence_id']}: "
                        f"{exc.error}: {exc.message}") from None
                results.append((row["line"], entry.occurrence_id, entry.offering_id, action))
            if not args.apply:
                db.session.rollback()
                for line, occurrence_id, offering_id, action in results:
                    print(f"line {line}: occurrence {occurrence_id} -> offering {offering_id} ({action}) [dry run]")
                print(f"\nValidated {len(results)} row(s); nothing written. Re-run with --apply to commit.")
                return 0
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f"FAIL: {exc}")
            print("Nothing was written; the file is applied atomically.")
            return 1
        for line, occurrence_id, offering_id, action in results:
            print(f"line {line}: occurrence {occurrence_id} -> offering {offering_id} ({action})")
        print(f"\nApplied {len(results)} mapping(s). Any occurrence still unmapped keeps "
              "HOD attendance reports disabled for every range.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
