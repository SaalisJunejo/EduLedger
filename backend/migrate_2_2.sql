BEGIN;

CREATE TABLE IF NOT EXISTS terms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT ck_terms_dates CHECK (end_date >= start_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_terms_single_active
    ON terms (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS sections (
    id SERIAL PRIMARY KEY,
    programme VARCHAR(64) NOT NULL,
    batch_year INTEGER NOT NULL,
    letter VARCHAR(4) NOT NULL,
    department VARCHAR(128) NOT NULL,
    CONSTRAINT uq_sections_programme_batch_letter
        UNIQUE (programme, batch_year, letter)
);
CREATE INDEX IF NOT EXISTS ix_sections_department
    ON sections (department);

CREATE TABLE IF NOT EXISTS term_offerings (
    id SERIAL PRIMARY KEY,
    term_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL,
    CONSTRAINT fk_term_offerings_term_id_terms
        FOREIGN KEY (term_id) REFERENCES terms (id),
    CONSTRAINT fk_term_offerings_section_id_sections
        FOREIGN KEY (section_id) REFERENCES sections (id),
    CONSTRAINT uq_term_offerings_term_section
        UNIQUE (term_id, section_id)
);
CREATE INDEX IF NOT EXISTS ix_term_offerings_term_id
    ON term_offerings (term_id);
CREATE INDEX IF NOT EXISTS ix_term_offerings_section_id
    ON term_offerings (section_id);

CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL,
    title VARCHAR(128) NOT NULL,
    department VARCHAR(128) NOT NULL,
    is_lab BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_courses_code_lower
    ON courses (lower(code));
CREATE INDEX IF NOT EXISTS ix_courses_department
    ON courses (department);

CREATE TABLE IF NOT EXISTS rooms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    type VARCHAR(16) NOT NULL,
    block VARCHAR(64),
    capacity INTEGER,
    department VARCHAR(128) NOT NULL,
    CONSTRAINT ck_rooms_type CHECK (type IN ('LECTURE', 'LAB')),
    CONSTRAINT ck_rooms_capacity CHECK (capacity IS NULL OR capacity > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rooms_name_lower
    ON rooms (lower(name));
CREATE INDEX IF NOT EXISTS ix_rooms_department
    ON rooms (department);

CREATE TABLE IF NOT EXISTS periods (
    id SERIAL PRIMARY KEY,
    ordinal INTEGER,
    start_time TIME WITHOUT TIME ZONE NOT NULL,
    end_time TIME WITHOUT TIME ZONE NOT NULL,
    is_break BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_periods_ordinal UNIQUE (ordinal),
    CONSTRAINT ck_periods_break_ordinal CHECK (
        (is_break AND ordinal IS NULL)
        OR (NOT is_break AND ordinal IS NOT NULL)
    ),
    CONSTRAINT ck_periods_times CHECK (end_time > start_time)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_periods_single_break
    ON periods (is_break) WHERE is_break;

COMMIT;