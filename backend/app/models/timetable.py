"""Timetable master data (Stage 2, PRD v2.2 sections 3 and 4.1, D11).

Six tables back the canonical grid and the term structure:

- ``Term``         - one semester; exactly one is active at a time.
- ``Section``      - a permanent cohort (``BS CS - 2023 - A``), never
  recreated per semester.
- ``TermOffering`` - a section's timetable page for one term, created blank
  for every section when a term is activated (D11).
- ``Course``       - unique on ``lower(code)``; ``is_lab`` drives the
  lab/room-type warning of Phase 2.4.
- ``Room``         - LECTURE or LAB, unique on ``lower(name)``.
- ``Period``       - the fixed grid: six 50-minute teaching periods plus one
  break row. Times are wall-clock times in TIMETABLE_TIMEZONE.

Sections, courses and rooms carry a ``department`` so HOD writes can be held
to the HOD's own department (`out_of_department`). `to_dict()` emits
camelCase, like every other model.
"""

from sqlalchemy import DDL, event

from ..extensions import db
from ..util import iso_z, utcnow

ROOM_LECTURE = "LECTURE"
ROOM_LAB = "LAB"
ROOM_TYPES = (ROOM_LECTURE, ROOM_LAB)


class Term(db.Model):
    """One semester. At most one row has ``is_active`` set."""

    __tablename__ = "terms"
    __table_args__ = (
        db.CheckConstraint("end_date >= start_date", name="ck_terms_dates"),
        # Enforce at most one active term; activation selects it (R4.1.1).
        db.Index(
            "uq_terms_single_active", "is_active", unique=True,
            postgresql_where=db.text("is_active"), sqlite_where=db.text("is_active"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self) -> dict:
        """JSON-friendly view of the term."""
        return {
            "id": self.id,
            "name": self.name,
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
            "isActive": self.is_active,
        }


class Section(db.Model):
    """A permanent student cohort, e.g. BS CS, batch 2023, section A."""

    __tablename__ = "sections"
    __table_args__ = (
        db.UniqueConstraint("programme", "batch_year", "letter", name="uq_sections_programme_batch_letter"),
    )

    id = db.Column(db.Integer, primary_key=True)
    programme = db.Column(db.String(64), nullable=False)
    batch_year = db.Column(db.Integer, nullable=False)
    letter = db.Column(db.String(4), nullable=False)
    department = db.Column(db.String(128), nullable=False, index=True)

    def to_dict(self) -> dict:
        """JSON-friendly view of the section."""
        return {
            "id": self.id,
            "programme": self.programme,
            "batchYear": self.batch_year,
            "letter": self.letter,
            "department": self.department,
            "label": f"{self.programme} \u00b7 {self.batch_year} \u00b7 {self.letter}",
        }


class TermOffering(db.Model):
    """A section's timetable page for one term (blank until Phase 2.4 slots)."""

    __tablename__ = "term_offerings"
    __table_args__ = (
        db.UniqueConstraint("term_id", "section_id", name="uq_term_offerings_term_section"),
    )

    id = db.Column(db.Integer, primary_key=True)
    term_id = db.Column(
        db.Integer, db.ForeignKey("terms.id", name="fk_term_offerings_term_id_terms"),
        nullable=False, index=True,
    )
    section_id = db.Column(
        db.Integer, db.ForeignKey("sections.id", name="fk_term_offerings_section_id_sections"),
        nullable=False, index=True,
    )

    term = db.relationship("Term", backref="offerings")
    section = db.relationship("Section", backref="offerings")

    def to_dict(self) -> dict:
        """JSON-friendly view of the offering."""
        return {
            "id": self.id,
            "termId": self.term_id,
            "sectionId": self.section_id,
        }


class Course(db.Model):
    """A course of a department; the code is unique case-insensitively."""

    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False)
    title = db.Column(db.String(128), nullable=False)
    department = db.Column(db.String(128), nullable=False, index=True)
    is_lab = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        db.Index("uq_courses_code_lower", db.func.lower(code), unique=True),
    )

    def to_dict(self) -> dict:
        """JSON-friendly view of the course."""
        return {
            "id": self.id,
            "code": self.code,
            "title": self.title,
            "department": self.department,
            "isLab": self.is_lab,
        }


class Room(db.Model):
    """A teaching room (LECTURE or LAB); the name is unique case-insensitively."""

    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    type = db.Column(db.String(16), nullable=False)
    block = db.Column(db.String(64), nullable=True)
    capacity = db.Column(db.Integer, nullable=True)
    # Owning department - scopes HOD writes (R4.1.7). Any department's slot
    # may still use the room once Phase 2.4 lands.
    department = db.Column(db.String(128), nullable=False, index=True)

    __table_args__ = (
        db.CheckConstraint("type IN ('LECTURE', 'LAB')", name="ck_rooms_type"),
        db.CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_rooms_capacity"),
        db.Index("uq_rooms_name_lower", db.func.lower(name), unique=True),
    )

    def to_dict(self) -> dict:
        """JSON-friendly view of the room."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "block": self.block,
            "capacity": self.capacity,
            "department": self.department,
        }


class Period(db.Model):
    """One row of the fixed grid: a teaching period (ordinal 1-6) or the break.

    The break row has no ordinal (slots reference teaching periods by
    ordinal, R4.3.4) and at most one break row exists.
    """

    __tablename__ = "periods"
    __table_args__ = (
        db.UniqueConstraint("ordinal", name="uq_periods_ordinal"),
        db.CheckConstraint(
            "(is_break AND ordinal IS NULL) OR (NOT is_break AND ordinal IS NOT NULL)",
            name="ck_periods_break_ordinal",
        ),
        db.CheckConstraint("end_time > start_time", name="ck_periods_times"),
        db.Index(
            "uq_periods_single_break", "is_break", unique=True,
            postgresql_where=db.text("is_break"), sqlite_where=db.text("is_break"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    ordinal = db.Column(db.Integer, nullable=True)
    # Wall-clock times in TIMETABLE_TIMEZONE, not UTC.
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    is_break = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self) -> dict:
        """JSON-friendly view of the period."""
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            "label": "Break" if self.is_break else f"P{self.ordinal}",
            "startTime": self.start_time.strftime("%H:%M"),
            "endTime": self.end_time.strftime("%H:%M"),
            "isBreak": self.is_break,
        }


class TimetableSlot(db.Model):
    """One weekly teaching cell; two adjacent lab cells may share a group ID."""

    __tablename__ = "timetable_slots"
    __table_args__ = (
        db.UniqueConstraint("offering_id", "day_of_week", "period_id", name="uq_timetable_slots_cell"),
        db.CheckConstraint("day_of_week BETWEEN 0 AND 4", name="ck_timetable_slots_day"),
        db.Index("ix_timetable_slots_teacher_cell", "faculty_id", "day_of_week", "period_id"),
        db.Index("ix_timetable_slots_room_cell", "room_id", "day_of_week", "period_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    offering_id = db.Column(db.Integer, db.ForeignKey("term_offerings.id"), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=False)
    session_group_id = db.Column(db.String(36), nullable=True)

    offering = db.relationship("TermOffering", backref="slots")
    course = db.relationship("Course")
    faculty = db.relationship("User")
    room = db.relationship("Room")
    period = db.relationship("Period")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "offeringId": self.offering_id, "courseId": self.course_id,
            "facultyId": self.faculty_id, "roomId": self.room_id,
            "dayOfWeek": self.day_of_week, "periodId": self.period_id,
            "periodOrdinal": self.period.ordinal, "sessionGroupId": self.session_group_id,
        }


# CHECK constraints cannot query another table. PostgreSQL's slot trigger
# protects raw writes without changing periods; periods have no write endpoint.
# Register the same guard for a fresh database created by the app factory.
event.listen(TimetableSlot.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION timetable_slot_teaching_period() RETURNS trigger AS $$
BEGIN
    PERFORM 1 FROM periods WHERE id = NEW.period_id
        AND NOT is_break AND ordinal BETWEEN 1 AND 6 FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'slot must reference a teaching period (1-6), never the break'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_timetable_slots_teaching_period';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""").execute_if(dialect="postgresql"))
event.listen(TimetableSlot.__table__, "after_create", DDL("""
CREATE TRIGGER timetable_slots_teaching_period
BEFORE INSERT OR UPDATE OF period_id ON timetable_slots
FOR EACH ROW EXECUTE FUNCTION timetable_slot_teaching_period();
""").execute_if(dialect="postgresql"))


class DirectoryEntry(db.Model):
    """Imported identity; importing or editing it never moves a registered student."""

    __tablename__ = "directory_entries"

    id = db.Column(db.Integer, primary_key=True)
    roll_number = db.Column(db.String(64), nullable=False)
    full_name = db.Column(db.String(128), nullable=False)
    university_email = db.Column(db.String(255), nullable=False)
    programme = db.Column(db.String(64), nullable=False)
    batch_year = db.Column(db.Integer, nullable=False)
    section_letter = db.Column(db.String(1), nullable=False)
    department = db.Column(db.String(128), nullable=False)
    claimed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("roll_number", name="uq_directory_entries_roll_number"),
        db.Index("uq_directory_entries_email_lower", db.func.lower(university_email), unique=True),
        db.CheckConstraint("batch_year BETWEEN 1900 AND 9999", name="ck_directory_entries_batch_year"),
        db.CheckConstraint("section_letter BETWEEN 'A' AND 'Z'", name="ck_directory_entries_letter"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "rollNumber": self.roll_number, "fullName": self.full_name,
            "universityEmail": self.university_email, "programme": self.programme,
            "batchYear": self.batch_year, "sectionLetter": self.section_letter,
            "department": self.department, "claimedBy": self.claimed_by,
        }


class StudentSection(db.Model):
    """Permanent membership, keyed by User.id (also the attendance Student.id)."""

    __tablename__ = "student_sections"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False, index=True)
    since = db.Column(db.DateTime, nullable=False, default=utcnow)
    section = db.relationship("Section")

    def to_dict(self) -> dict:
        return {"userId": self.user_id, "sectionId": self.section_id, "since": iso_z(self.since)}
