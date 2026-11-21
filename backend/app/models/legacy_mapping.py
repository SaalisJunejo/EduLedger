"""Explicit, auditable section mapping for legacy slotless occurrences.

A pre-Stage-2 ``ScheduledClass`` row has no ``slot_id``, so it has no
``TermOffering`` and therefore no section. Phase 2.9 reports need that
section for roster totals, percentages and HOD scope. Nothing may guess it
from the professor, course, room, time or captured students, so an operator
records the association here and signs it with a reason.

Design notes:

- ``occurrence_id`` deliberately has NO foreign key. Phase 2.5 D15 requires a
  closure to DELETE future unstarted occurrences; history must not block an
  authoritative holiday. A trigger supersedes an active mapping immediately
  before that deletion, keeping the row (and its snapshots) as provenance.
- ``offering_id`` has no foreign key either, for the same reason: superseded
  history must never be able to block the removal of a live parent. The offering
  is validated when the row is inserted (service and trigger), and the snapshots
  keep the decision readable afterwards.
- An ACTIVE row is different: it is the only thing that makes a slotless
  occurrence reportable, so deleting its offering would orphan it. A trigger on
  ``term_offerings`` therefore refuses to delete an offering while an active
  mapping references it, and a second trigger refuses to change its ``term_id``
  or ``section_id`` (the fields that decide the reporting scope); superseded
  rows never block either.
- The insert guard locks the offering ``FOR SHARE`` while it validates it, so a
  concurrent delete or scope change waits until the mapping commits. The
  offering guards take no lock on ``scheduled_classes``, so both paths acquire
  rows in the same order (scheduled_classes, then term_offerings) and cannot
  deadlock on each other.
- A deferred constraint trigger re-checks at COMMIT, the way a foreign key
  does. The immediate guards run with the deleting statement's snapshot, which
  cannot see a mapping that committed while that statement waited on the lock;
  the commit-time check takes a fresh snapshot and refuses the delete/scope
  change that would otherwise orphan or mis-scope the active row.
- Rows are append-only and undeletable. A trigger rejects every field edit, a
  second supersede and every DELETE. The only permitted update is setting
  ``superseded_at``, always paired with a new active row by the service.
- Snapshots of the occurrence's start time and course code make the history
  readable after the occurrence itself is gone.
"""

from sqlalchemy import DDL, event

from ..extensions import db
from ..util import iso_z, utcnow


class LegacyOccurrenceMapping(db.Model):
    """One operator decision linking a slotless occurrence to a TermOffering."""

    __tablename__ = "legacy_occurrence_mappings"
    __table_args__ = (
        db.CheckConstraint(
            "btrim(mapped_by) <> '' AND btrim(reason) <> ''",
            name="ck_legacy_occurrence_mappings_text",
        ),
        # Exactly one active decision per occurrence; history keeps the rest.
        db.Index(
            "uq_legacy_occurrence_mappings_active", "occurrence_id", unique=True,
            postgresql_where=db.text("superseded_at IS NULL"),
            sqlite_where=db.text("superseded_at IS NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    # No FK: a closure may delete the occurrence (D15) after superseding this row.
    occurrence_id = db.Column(db.Integer, nullable=False, index=True)
    offering_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )
    # ^ no FK: immutable history must not block deleting the offering. Insert
    #   time validation (service + trigger) is the authoritative check.
    # Snapshots taken at mapping time, retained after the occurrence is deleted.
    occurrence_start_time = db.Column(db.DateTime, nullable=False)
    occurrence_course_code = db.Column(db.String(64), nullable=False)

    mapped_by = db.Column(db.String(128), nullable=False)
    mapped_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reason = db.Column(db.Text, nullable=False)
    superseded_at = db.Column(db.DateTime, nullable=True)

    # Explicit join: the audit row carries no FK, so SQLAlchemy cannot infer
    # which side is foreign. `foreign()` marks the audit column, and
    # viewonly=True guarantees the history never writes to the offering.
    offering = db.relationship(
        "TermOffering",
        primaryjoin="foreign(LegacyOccurrenceMapping.offering_id) == TermOffering.id",
        viewonly=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "occurrenceId": self.occurrence_id,
            "offeringId": self.offering_id,
            "occurrenceStartTime": iso_z(self.occurrence_start_time),
            "occurrenceCourseCode": self.occurrence_course_code,
            "mappedBy": self.mapped_by,
            "mappedAt": iso_z(self.mapped_at),
            "reason": self.reason,
            "supersededAt": iso_z(self.superseded_at) if self.superseded_at else None,
            "active": self.superseded_at is None,
        }


# ---------------------------------------------------------------------------
# PostgreSQL guards. Registered for a fresh database created by the app
# factory; migrate_2_9.sql installs the same objects on an existing database.
#
# These are DDL event handlers on a Table, so SQLAlchemy compiles the string
# with ``statement % context`` (it supplies %(table)s and %(schema)s). Every
# literal '%' that must reach PL/pgSQL (the RAISE format placeholders) therefore
# has to be written as '%%' here. The compiled text, and the equivalent raw SQL
# in migrate_2_9.sql, keep the single '%'.
# ---------------------------------------------------------------------------
event.listen(LegacyOccurrenceMapping.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_guard() RETURNS trigger AS $$
DECLARE
    row_slot_id   integer;
    row_term_id   integer;
    row_start     timestamp;
    row_course    varchar(64);
    offer_term_id integer;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'mapping %% is already superseded; history is append-only', OLD.id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legacy_occurrence_mappings_append_only';
        END IF;
        IF NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id
           OR NEW.offering_id IS DISTINCT FROM OLD.offering_id
           OR NEW.occurrence_start_time IS DISTINCT FROM OLD.occurrence_start_time
           OR NEW.occurrence_course_code IS DISTINCT FROM OLD.occurrence_course_code
           OR NEW.mapped_by IS DISTINCT FROM OLD.mapped_by
           OR NEW.mapped_at IS DISTINCT FROM OLD.mapped_at
           OR NEW.reason IS DISTINCT FROM OLD.reason THEN
            RAISE EXCEPTION 'mapping %% fields are immutable; supersede it instead', OLD.id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legacy_occurrence_mappings_append_only';
        END IF;
        IF NEW.superseded_at IS NULL THEN
            RAISE EXCEPTION 'the only permitted mapping update is setting superseded_at'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legacy_occurrence_mappings_append_only';
        END IF;
        RETURN NEW;
    END IF;

    SELECT slot_id, term_id, start_time, course_code
      INTO row_slot_id, row_term_id, row_start, row_course
      FROM scheduled_classes WHERE id = NEW.occurrence_id FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'occurrence %% does not exist', NEW.occurrence_id
            USING ERRCODE = '23503';
    END IF;
    IF row_slot_id IS NOT NULL THEN
        RAISE EXCEPTION 'occurrence %% already has slot_id %%; it is not legacy',
            NEW.occurrence_id, row_slot_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_slotless';
    END IF;
    IF NEW.occurrence_start_time IS DISTINCT FROM row_start
       OR NEW.occurrence_course_code IS DISTINCT FROM row_course THEN
        RAISE EXCEPTION 'mapping snapshots for occurrence %% do not match the row',
            NEW.occurrence_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_snapshot';
    END IF;

    -- FOR SHARE: a concurrent DELETE or scope UPDATE of this offering needs
    -- FOR UPDATE / FOR NO KEY UPDATE on the row and therefore waits here until
    -- this mapping transaction ends. Both paths then acquire rows in the same
    -- order (scheduled_classes, then term_offerings), and the offering guards
    -- never lock scheduled_classes, so no lock cycle exists.
    SELECT term_id INTO offer_term_id FROM term_offerings WHERE id = NEW.offering_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'offering %% does not exist', NEW.offering_id
            USING ERRCODE = '23503';
    END IF;
    IF row_term_id IS NOT NULL AND row_term_id <> offer_term_id THEN
        RAISE EXCEPTION
            'occurrence %% has term_id %% but offering %% belongs to term %%',
            NEW.occurrence_id, row_term_id, NEW.offering_id, offer_term_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_term';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""").execute_if(dialect="postgresql"))

event.listen(LegacyOccurrenceMapping.__table__, "after_create", DDL("""
DROP TRIGGER IF EXISTS legacy_occurrence_mappings_guard ON legacy_occurrence_mappings;
CREATE TRIGGER legacy_occurrence_mappings_guard
BEFORE INSERT OR UPDATE ON legacy_occurrence_mappings
FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_guard();
""").execute_if(dialect="postgresql"))

event.listen(LegacyOccurrenceMapping.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_delete_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'mapping history is immutable: rows cannot be deleted, only superseded'
        USING ERRCODE = '23514',
              CONSTRAINT = 'ck_legacy_occurrence_mappings_append_only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS legacy_occurrence_mappings_delete_guard ON legacy_occurrence_mappings;
CREATE TRIGGER legacy_occurrence_mappings_delete_guard
BEFORE DELETE ON legacy_occurrence_mappings
FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_delete_guard();
""").execute_if(dialect="postgresql"))


# The closure path deletes ScheduledClass rows directly (D15). Supersede the
# active mapping first so history survives and the delete is never blocked.
# Attached to scheduled_classes because that table is created first on a fresh
# database; the function body only resolves the mapping table at run time.
from .workload import ScheduledClass  # noqa: E402  (avoid a models import cycle)

event.listen(ScheduledClass.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_release() RETURNS trigger AS $$
BEGIN
    UPDATE legacy_occurrence_mappings
       SET superseded_at = (now() AT TIME ZONE 'UTC')
     WHERE occurrence_id = OLD.id AND superseded_at IS NULL;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scheduled_classes_release_legacy_mapping ON scheduled_classes;
CREATE TRIGGER scheduled_classes_release_legacy_mapping
BEFORE DELETE ON scheduled_classes
FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_release();
""").execute_if(dialect="postgresql"))


# An ACTIVE mapping is the only route by which a slotless occurrence is
# reportable, so deleting its offering would orphan it. Superseded rows are
# history and deliberately do NOT block the delete (that is why offering_id
# carries no FK). Registered on term_offerings so the trigger target already
# exists; the function body resolves the mapping table only when it runs.
from .timetable import TermOffering  # noqa: E402  (models package import order)

event.listen(TermOffering.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION term_offering_active_mapping_guard() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'offering %% still has an active legacy occurrence mapping; supersede it first', OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_active_legacy_mapping';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_active_mapping_guard ON term_offerings;
CREATE TRIGGER term_offerings_active_mapping_guard
BEFORE DELETE ON term_offerings
FOR EACH ROW EXECUTE FUNCTION term_offering_active_mapping_guard();
""").execute_if(dialect="postgresql"))


# The offering's term and section decide the mapping's reporting scope. Once an
# active mapping references the offering, changing them would silently move an
# already-reported occurrence into another term or department.
event.listen(TermOffering.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION term_offering_scope_guard() RETURNS trigger AS $$
BEGIN
    IF (NEW.term_id IS DISTINCT FROM OLD.term_id
        OR NEW.section_id IS DISTINCT FROM OLD.section_id)
       AND EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                    WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'offering %% term/section is fixed while an active legacy occurrence mapping references it',
            OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_scope_legacy_mapping';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_scope_guard ON term_offerings;
CREATE TRIGGER term_offerings_scope_guard
BEFORE UPDATE OF term_id, section_id ON term_offerings
FOR EACH ROW EXECUTE FUNCTION term_offering_scope_guard();
""").execute_if(dialect="postgresql"))


# Commit-time backstop. The BEFORE guards run with the mutating statement's
# snapshot, so a delete that waited on the insert guard's FOR SHARE lock cannot
# see the mapping that just committed. A deferred constraint trigger runs at
# COMMIT with a fresh snapshot (the mechanism foreign keys use) and refuses the
# mutation there, so an active row can never end up orphaned or mis-scoped.
event.listen(TermOffering.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION term_offering_active_mapping_commit_check() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                    WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
            RAISE EXCEPTION
                'offering %% still has an active legacy occurrence mapping; supersede it first', OLD.id
                USING ERRCODE = '23503',
                      CONSTRAINT = 'ck_term_offerings_active_legacy_mapping';
        END IF;
        RETURN NULL;
    END IF;

    IF (NEW.term_id IS DISTINCT FROM OLD.term_id
        OR NEW.section_id IS DISTINCT FROM OLD.section_id)
       AND EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                    WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'offering %% term/section is fixed while an active legacy occurrence mapping references it',
            OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_scope_legacy_mapping';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_active_mapping_commit_check ON term_offerings;
CREATE CONSTRAINT TRIGGER term_offerings_active_mapping_commit_check
AFTER DELETE OR UPDATE OF term_id, section_id ON term_offerings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION term_offering_active_mapping_commit_check();
""").execute_if(dialect="postgresql"))
