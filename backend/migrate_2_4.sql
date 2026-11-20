BEGIN;

CREATE TABLE IF NOT EXISTS timetable_slots (
    id SERIAL PRIMARY KEY,
    offering_id INTEGER NOT NULL REFERENCES term_offerings(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    faculty_id INTEGER NOT NULL REFERENCES users(id),
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    day_of_week INTEGER NOT NULL,
    period_id INTEGER NOT NULL REFERENCES periods(id),
    session_group_id VARCHAR(36),
    CONSTRAINT uq_timetable_slots_cell
        UNIQUE (offering_id, day_of_week, period_id),
    CONSTRAINT ck_timetable_slots_day
        CHECK (day_of_week BETWEEN 0 AND 4)
);

CREATE INDEX IF NOT EXISTS ix_timetable_slots_offering_id
    ON timetable_slots(offering_id);
CREATE INDEX IF NOT EXISTS ix_timetable_slots_teacher_cell
    ON timetable_slots(faculty_id, day_of_week, period_id);
CREATE INDEX IF NOT EXISTS ix_timetable_slots_room_cell
    ON timetable_slots(room_id, day_of_week, period_id);

CREATE OR REPLACE FUNCTION timetable_slot_teaching_period()
RETURNS trigger AS $$
BEGIN
    PERFORM 1 FROM periods
        WHERE id = NEW.period_id
          AND NOT is_break
          AND ordinal BETWEEN 1 AND 6
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'slot must reference a teaching period (1-6), never the break'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_timetable_slots_teaching_period';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS timetable_slots_teaching_period
    ON timetable_slots;
CREATE TRIGGER timetable_slots_teaching_period
    BEFORE INSERT OR UPDATE OF period_id ON timetable_slots
    FOR EACH ROW EXECUTE FUNCTION timetable_slot_teaching_period();

COMMIT;