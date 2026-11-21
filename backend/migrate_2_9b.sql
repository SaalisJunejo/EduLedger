-- Phase 2.9b: capture-time evidence columns on attendance_check_ins.
--
-- Phase 2.9 added `method` and `device_verified` to the AttendanceCheckIn
-- model (backend/app/models/session.py) but no migration ever created them.
-- db.create_all() only issues CREATE TABLE for MISSING tables, so a database
-- whose attendance_check_ins predates that model change is never altered and
-- every report query that selects the model fails with
--   column attendance_check_ins.method does not exist
-- (sessions, matrix, roster, download and verify all select the model).
--
-- This file is additive and rerunnable. It adds the two columns the ORM
-- expects, both nullable with no default, and the model's named check
-- constraint. Existing rows keep NULL in both columns, which is the intended
-- meaning - unknown - and is never inferred from a student's current device
-- registration or the session namespace. There is no backfill, no UPDATE and
-- no DELETE here.
--
-- Prerequisite: attendance_check_ins must already exist (run the app factory
-- once against the target database), like migrate_2_4.sql and
-- migrate_2_9.sql.
--
-- Rerunnable: each column is added only if missing and the constraint is
-- added only when absent. A pre-existing constraint with this name must match
-- the model's definition, otherwise the file aborts and changes nothing.

BEGIN;

-- Guard: this patch is written for the development database only. On any
-- other database the whole transaction aborts before the first ALTER.
DO $$
BEGIN
    IF current_database() <> 'eduledger_db' THEN
        RAISE EXCEPTION
            'refusing to run migrate_2_9b.sql on database %: expected eduledger_db',
            current_database();
    END IF;
END
$$;

-- method: how the check-in was captured - 'qr' for a rotating-QR scan,
-- 'numeric_code' for a code typed from the projector. NULL for rows written
-- before this column existed (unknown), never backfilled. VARCHAR(16) matches
-- the model.
-- device_verified: whether the capture was device-verified at the time.
-- NULL for rows written before this column existed (unknown).
ALTER TABLE attendance_check_ins
    ADD COLUMN IF NOT EXISTS method VARCHAR(16),
    ADD COLUMN IF NOT EXISTS device_verified BOOLEAN;

-- The model declares ck_attendance_check_ins_method, which create_all cannot
-- add to an existing table. The existing definition is not compared as text
-- because pg_get_constraintdef formatting varies between PostgreSQL versions;
-- an existing constraint is accepted only if it (a) is a CHECK, (b) covers
-- only `method`, and (c) names exactly the literals 'qr' and 'numeric_code'.
-- Anything else aborts instead of being silently accepted.
DO $$
DECLARE
    existing_kind TEXT;
    existing_columns SMALLINT[];
    existing_definition TEXT;
    method_attnum SMALLINT;
    allowed_values TEXT[];
BEGIN
    SELECT attnum INTO method_attnum
      FROM pg_attribute
     WHERE attrelid = 'attendance_check_ins'::regclass
       AND attname = 'method'
       AND NOT attisdropped;

    SELECT con.contype::text, con.conkey, pg_get_constraintdef(con.oid)
      INTO existing_kind, existing_columns, existing_definition
      FROM pg_constraint con
     WHERE con.conrelid = 'attendance_check_ins'::regclass
       AND con.conname = 'ck_attendance_check_ins_method';

    IF NOT FOUND THEN
        ALTER TABLE attendance_check_ins
            ADD CONSTRAINT ck_attendance_check_ins_method
            CHECK (method IS NULL OR method IN ('qr', 'numeric_code'));
    ELSE
        SELECT array_agg(DISTINCT lower(match[1]) ORDER BY lower(match[1]))
          INTO allowed_values
          FROM regexp_matches(existing_definition, '''([^'']*)''', 'g') AS match;

        IF existing_kind <> 'c'
           OR existing_columns IS DISTINCT FROM ARRAY[method_attnum]::SMALLINT[]
           OR allowed_values IS DISTINCT FROM ARRAY['numeric_code', 'qr']::TEXT[] THEN
            RAISE EXCEPTION
                'ck_attendance_check_ins_method already exists with a different definition: %',
                existing_definition
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_attendance_check_ins_method';
        END IF;
    END IF;
END
$$;

COMMIT;
