BEGIN;

ALTER TABLE student_profiles
    ADD COLUMN IF NOT EXISTS programme VARCHAR(64);

CREATE TABLE IF NOT EXISTS directory_entries (
    id SERIAL PRIMARY KEY,
    roll_number VARCHAR(64) NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    university_email VARCHAR(255) NOT NULL,
    programme VARCHAR(64) NOT NULL,
    batch_year INTEGER NOT NULL,
    section_letter VARCHAR(1) NOT NULL,
    department VARCHAR(128) NOT NULL,
    claimed_by INTEGER REFERENCES users(id),
    CONSTRAINT uq_directory_entries_roll_number UNIQUE (roll_number),
    CONSTRAINT ck_directory_entries_batch_year
        CHECK (batch_year BETWEEN 1900 AND 9999),
    CONSTRAINT ck_directory_entries_letter
        CHECK (section_letter BETWEEN 'A' AND 'Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_directory_entries_email_lower
    ON directory_entries (lower(university_email));

CREATE TABLE IF NOT EXISTS student_sections (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    section_id INTEGER NOT NULL REFERENCES sections(id),
    since TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_student_sections_section_id
    ON student_sections(section_id);

COMMIT;