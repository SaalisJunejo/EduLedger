-- Phase 2.9: explicit, auditable mapping of legacy slotless occurrences.
--
-- A pre-Stage-2 scheduled_classes row has no slot_id, so it has no
-- term_offering and no section. Nothing may infer one from the professor,
-- course, room, time or the students who happened to be captured; an operator
-- records the association here with a reason.
--
-- occurrence_id deliberately has NO foreign key. Phase 2.5 D15 requires a
-- closure to DELETE future unstarted occurrences, and history must not block
-- that. The release trigger supersedes an active mapping immediately before
-- the delete, so the row (with its snapshots) survives as provenance.
-- offering_id has no foreign key either: SUPERSEDED history must never block
-- deleting a live offering. It is validated on INSERT instead, with a locking
-- (FOR SHARE) read of the offering so a concurrent delete or term/section
-- change waits for the mapping to commit. An ACTIVE row is protected on three
-- levels: a BEFORE DELETE trigger, a BEFORE UPDATE OF term_id, section_id
-- trigger, and a deferred constraint trigger that re-checks at COMMIT with a
-- fresh snapshot (the way a foreign key does), closing the window where a
-- delete waits on the insert lock and cannot yet see the committed mapping.
--
-- Rows are append-only and undeletable (see the delete guard below). Testing
-- therefore expects a DISPOSABLE database, and every run creates a FRESH,
-- uniquely named one. Nothing here drops a database; fixture cleanup never
-- deletes audit rows and the new database replaces the previous run's.
--
-- Prerequisite: the base schema must already exist (run the app factory once
-- against the target database), because this file references scheduled_classes
-- and term_offerings.
--
-- Rerunnable: every object is created only if missing (or replaced).

BEGIN;

CREATE TABLE IF NOT EXISTS legacy_occurrence_mappings (
    id SERIAL PRIMARY KEY,
    -- No FK: D15 deletes the occurrence while this history row remains.
    occurrence_id INTEGER NOT NULL,
    offering_id INTEGER NOT NULL,
    -- Snapshots taken at mapping time, readable after the occurrence is gone.
    occurrence_start_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    occurrence_course_code VARCHAR(64) NOT NULL,
    mapped_by VARCHAR(128) NOT NULL,
    mapped_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    reason TEXT NOT NULL,
    superseded_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT ck_legacy_occurrence_mappings_text
        CHECK (btrim(mapped_by) <> '' AND btrim(reason) <> '')
);

-- Immutability rule: no FK may make an audit row block a live parent. Drops
-- the constraint created by an earlier revision of this migration.
ALTER TABLE legacy_occurrence_mappings
    DROP CONSTRAINT IF EXISTS fk_legacy_occurrence_mappings_offering_id;

CREATE INDEX IF NOT EXISTS ix_legacy_occurrence_mappings_occurrence_id
    ON legacy_occurrence_mappings(occurrence_id);
CREATE INDEX IF NOT EXISTS ix_legacy_occurrence_mappings_offering_id
    ON legacy_occurrence_mappings(offering_id);

-- Exactly one active decision per occurrence; superseded rows are history.
CREATE UNIQUE INDEX IF NOT EXISTS uq_legacy_occurrence_mappings_active
    ON legacy_occurrence_mappings(occurrence_id)
    WHERE superseded_at IS NULL;

-- Append-only rows: INSERT validates the occurrence and offering, UPDATE may
-- only set superseded_at (once), and a row cannot be edited after supersede.
-- DELETE is always rejected (see the delete guard).
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_guard()
RETURNS trigger AS $$
DECLARE
    row_slot_id   INTEGER;
    row_term_id   INTEGER;
    row_start     TIMESTAMP;
    row_course    VARCHAR(64);
    offer_term_id INTEGER;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'mapping % is already superseded; history is append-only', OLD.id
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
            RAISE EXCEPTION 'mapping % fields are immutable; supersede it instead', OLD.id
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
        RAISE EXCEPTION 'occurrence % does not exist', NEW.occurrence_id
            USING ERRCODE = '23503';
    END IF;
    IF row_slot_id IS NOT NULL THEN
        RAISE EXCEPTION 'occurrence % already has slot_id %; it is not legacy',
            NEW.occurrence_id, row_slot_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_slotless';
    END IF;
    IF NEW.occurrence_start_time IS DISTINCT FROM row_start
       OR NEW.occurrence_course_code IS DISTINCT FROM row_course THEN
        RAISE EXCEPTION 'mapping snapshots for occurrence % do not match the row',
            NEW.occurrence_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_snapshot';
    END IF;

    -- FOR SHARE: a concurrent DELETE or scope UPDATE of this offering needs
    -- FOR UPDATE / FOR NO KEY UPDATE on the row and therefore waits here until
    -- this mapping transaction ends. Lock order in both paths is
    -- scheduled_classes -> term_offerings, and the offering guards never lock
    -- scheduled_classes, so no cycle exists.
    SELECT term_id INTO offer_term_id FROM term_offerings WHERE id = NEW.offering_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'offering % does not exist', NEW.offering_id
            USING ERRCODE = '23503';
    END IF;
    IF row_term_id IS NOT NULL AND row_term_id <> offer_term_id THEN
        RAISE EXCEPTION 'occurrence % has term_id % but offering % belongs to term %',
            NEW.occurrence_id, row_term_id, NEW.offering_id, offer_term_id
            USING ERRCODE = '23514', CONSTRAINT = 'ck_legacy_occurrence_mappings_term';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS legacy_occurrence_mappings_guard
    ON legacy_occurrence_mappings;
CREATE TRIGGER legacy_occurrence_mappings_guard
    BEFORE INSERT OR UPDATE ON legacy_occurrence_mappings
    FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_guard();

-- History can never be deleted, not even after the occurrence is gone.
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_delete_guard()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'mapping history is immutable: rows cannot be deleted, only superseded'
        USING ERRCODE = '23514',
              CONSTRAINT = 'ck_legacy_occurrence_mappings_append_only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS legacy_occurrence_mappings_delete_guard
    ON legacy_occurrence_mappings;
CREATE TRIGGER legacy_occurrence_mappings_delete_guard
    BEFORE DELETE ON legacy_occurrence_mappings
    FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_delete_guard();

-- Phase 2.5 D15: a closure deletes the occurrence. Release its active mapping
-- in the same statement so history is kept and nothing blocks the delete.
CREATE OR REPLACE FUNCTION legacy_occurrence_mapping_release()
RETURNS trigger AS $$
BEGIN
    UPDATE legacy_occurrence_mappings
       SET superseded_at = (now() AT TIME ZONE 'UTC')
     WHERE occurrence_id = OLD.id AND superseded_at IS NULL;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scheduled_classes_release_legacy_mapping
    ON scheduled_classes;
CREATE TRIGGER scheduled_classes_release_legacy_mapping
    BEFORE DELETE ON scheduled_classes
    FOR EACH ROW EXECUTE FUNCTION legacy_occurrence_mapping_release();

-- An ACTIVE mapping is the only route by which a slotless occurrence becomes
-- reportable, so deleting its offering would orphan it. Superseded rows are
-- history and deliberately do not block the delete (offering_id has no FK).
-- Applies to ORM deletes and direct SQL alike; superseding the mapping first
-- is the only way to release the offering.
CREATE OR REPLACE FUNCTION term_offering_active_mapping_guard()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'offering % still has an active legacy occurrence mapping; supersede it first', OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_active_legacy_mapping';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_active_mapping_guard
    ON term_offerings;
CREATE TRIGGER term_offerings_active_mapping_guard
    BEFORE DELETE ON term_offerings
    FOR EACH ROW EXECUTE FUNCTION term_offering_active_mapping_guard();

-- The offering's term and section decide the mapping's reporting scope. Once an
-- active mapping references the offering, changing them would silently move an
-- already-reported occurrence into another term or department.
CREATE OR REPLACE FUNCTION term_offering_scope_guard()
RETURNS trigger AS $$
BEGIN
    IF (NEW.term_id IS DISTINCT FROM OLD.term_id
        OR NEW.section_id IS DISTINCT FROM OLD.section_id)
       AND EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                    WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'offering % term/section is fixed while an active legacy occurrence mapping references it',
            OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_scope_legacy_mapping';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_scope_guard
    ON term_offerings;
CREATE TRIGGER term_offerings_scope_guard
    BEFORE UPDATE OF term_id, section_id ON term_offerings
    FOR EACH ROW EXECUTE FUNCTION term_offering_scope_guard();

-- Commit-time backstop: the BEFORE guards use the mutating statement's
-- snapshot, so a delete that waited on the insert's FOR SHARE lock cannot see
-- the mapping that just committed. A deferred constraint trigger runs at COMMIT
-- with a fresh snapshot (the foreign-key mechanism) and refuses it there.
CREATE OR REPLACE FUNCTION term_offering_active_mapping_commit_check()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM legacy_occurrence_mappings
                    WHERE offering_id = OLD.id AND superseded_at IS NULL) THEN
            RAISE EXCEPTION
                'offering % still has an active legacy occurrence mapping; supersede it first', OLD.id
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
            'offering % term/section is fixed while an active legacy occurrence mapping references it',
            OLD.id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'ck_term_offerings_scope_legacy_mapping';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS term_offerings_active_mapping_commit_check
    ON term_offerings;
CREATE CONSTRAINT TRIGGER term_offerings_active_mapping_commit_check
    AFTER DELETE OR UPDATE OF term_id, section_id ON term_offerings
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION term_offering_active_mapping_commit_check();

COMMIT;
