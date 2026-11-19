"""Atomic directory planning/import (PRD v2.2 R4.2.2-4). No account creation."""

import csv
import io
import re

from flask import current_app

from ..extensions import db
from ..models import Course, DirectoryEntry, InstitutionWhitelist, Room, Section

COLUMNS = (
    "roll_number", "full_name", "university_email", "programme", "batch_year",
    "section_letter", "department",
)
LIMITS = {"roll_number": 64, "full_name": 128, "university_email": 255,
          "programme": 64, "department": 128}
MAX_ROWS = 5000


def known_departments():
    """No department table exists: use the staff whitelist and master data."""
    names = set()
    for model in (InstitutionWhitelist, Section, Course, Room):
        names.update(value for (value,) in db.session.query(model.department).distinct().all() if value)
    return {name.casefold(): name for name in sorted(names)}


def plan_import(data: bytes, *, lock=False):
    """Return the same proposed counts/errors for dry runs and actual imports."""
    report = {"rows_created": 0, "rows_updated": 0, "rows_unchanged": 0,
              "sections_created": 0, "rows_rejected": []}
    rejected = {}

    def reject(line, reason):
        rejected.setdefault(line, []).append(reason)

    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = next(reader, None)
    if header is None:
        raise ValueError("file is empty; headers are required")
    header = [name.strip() for name in header]
    if len(header) != len(set(header)) or any(not name for name in header):
        raise ValueError("headers must be nonempty and unique")
    missing = sorted(set(COLUMNS) - set(header))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    departments = known_departments()
    directory_query = DirectoryEntry.query.order_by(DirectoryEntry.id)
    section_query = Section.query.order_by(Section.id)
    if lock:
        directory_query = directory_query.with_for_update()
        section_query = section_query.with_for_update()
    directory = directory_query.all()
    existing = {row.roll_number: row for row in directory}
    email_owners = {row.university_email.lower(): row.roll_number for row in directory}
    sections = {(row.programme, row.batch_year, row.letter): row for row in section_query.all()}
    rolls, emails, section_lines = {}, {}, {}
    records = []
    domain = current_app.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    while True:
        line = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            reject(line, f"malformed CSV: {exc}")
            break
        if len(records) >= MAX_ROWS:
            raise ValueError(f"file exceeds {MAX_ROWS} rows")
        if len(cells) != len(header):
            reject(line, "row has a different number of cells than the header")
            records.append((line, None))
            continue
        values = {key: value.strip() for key, value in zip(header, cells) if key in COLUMNS}
        for field in COLUMNS:
            if not values[field]:
                reject(line, f"{field} is required")
        for field, limit in LIMITS.items():
            if len(values[field]) > limit:
                reject(line, f"{field} exceeds {limit} characters")
        email = values["university_email"].lower()
        values["university_email"] = email
        if not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
            reject(line, "invalid university_email")
        else:
            email_domain = email.rsplit("@", 1)[1]
            if email_domain != domain and not email_domain.endswith("." + domain):
                reject(line, f"email must belong to @{domain}")
        department = departments.get(values["department"].casefold())
        if department is None:
            reject(line, "department is not known in the staff whitelist or timetable master data")
        else:
            values["department"] = department
        year = values["batch_year"]
        if not (year.isascii() and year.isdigit() and len(year) == 4 and 1900 <= int(year) <= 9999):
            reject(line, "batch_year must be a four-digit year from 1900 to 9999")
        else:
            values["batch_year"] = int(year)
        if not re.fullmatch(r"[A-Z]", values["section_letter"]):
            reject(line, "section_letter must be a single A-Z")
        roll = values["roll_number"]
        for value, seen, field in ((roll, rolls, "roll_number"), (email, emails, "university_email")):
            if value in seen:
                reject(line, f"duplicate {field} (also on line {seen[value]})")
                reject(seen[value], f"duplicate {field} (also on line {line})")
            else:
                seen[value] = line
        if email in email_owners and email_owners[email] != roll:
            reject(line, "university_email belongs to a different directory roll")
        if isinstance(values["batch_year"], int):
            key = (values["programme"], values["batch_year"], values["section_letter"])
            if key in sections and sections[key].department != values["department"]:
                reject(line, "existing section belongs to another department")
            if key in section_lines:
                prior_line, prior_department = section_lines[key]
                if prior_department != values["department"]:
                    reject(line, "same section has conflicting departments in this file")
                    reject(prior_line, "same section has conflicting departments in this file")
            else:
                section_lines[key] = (line, values["department"])
        records.append((line, values))

    report["rows_rejected"] = [{"line": line, "row": line, "reasons": reasons}
                               for line, reasons in sorted(rejected.items())]
    if rejected:
        return report, [], {}, existing
    new_sections = {}
    for _, values in records:
        row = existing.get(values["roll_number"])
        if row is None:
            report["rows_created"] += 1
        elif any(getattr(row, key) != value for key, value in values.items()):
            report["rows_updated"] += 1
        else:
            report["rows_unchanged"] += 1
        key = (values["programme"], values["batch_year"], values["section_letter"])
        if key not in sections:
            new_sections[key] = values["department"]
    report["sections_created"] = len(new_sections)
    return report, records, new_sections, existing


def apply_import(records, new_sections, existing):
    """Caller commits once; claimed_by and permanent membership are untouched."""
    for (programme, batch_year, letter), department in new_sections.items():
        db.session.add(Section(programme=programme, batch_year=batch_year,
                               letter=letter, department=department))
    for _, values in records:
        row = existing.get(values["roll_number"])
        if row is None:
            db.session.add(DirectoryEntry(**values))
        else:
            for field, value in values.items():
                setattr(row, field, value)
