CREATE TABLE users (
    id uuid PRIMARY KEY,
    login text NOT NULL UNIQUE
);
INSERT INTO users VALUES ('00000000-0000-4000-8000-000000000001', 'local');
CREATE TABLE conferences (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text CHECK (title IS NULL OR length(title) > 0),
    start_date date, end_date date, place text, organizer text,
    version integer NOT NULL CHECK (version > 0),
    edited_by uuid NOT NULL REFERENCES users(id),
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
);
CREATE TABLE sections (
    id uuid PRIMARY KEY,
    conference_id uuid NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
    title text NOT NULL CHECK (length(title) > 0),
    date date, hall text, "order" integer NOT NULL CHECK ("order" > 0),
    version integer NOT NULL CHECK (version > 0),
    edited_by uuid NOT NULL REFERENCES users(id)
);
CREATE INDEX sections_conference ON sections(conference_id);
CREATE TABLE people (
    id uuid PRIMARY KEY, full_name text NOT NULL CHECK (length(full_name) > 0), affiliation text,
    version integer NOT NULL CHECK (version > 0), edited_by uuid NOT NULL REFERENCES users(id)
);
CREATE TABLE talks (
    id uuid PRIMARY KEY, section_id uuid NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    "order" integer NOT NULL CHECK ("order" > 0), title text, date date,
    talk_type text CHECK (talk_type IN ('исследовательский', 'обзорный', 'прикладной', 'иное')),
    status text NOT NULL CHECK (status IN (
        'draft', 'matched', 'processed', 'reviewed', 'stale', 'not_held', 'insufficient_data'
    )),
    match_confidence double precision CHECK (match_confidence BETWEEN 0 AND 1),
    version integer NOT NULL CHECK (version > 0), edited_by uuid NOT NULL REFERENCES users(id),
    CHECK (status != 'insufficient_data' OR title IS NOT NULL)
);
CREATE INDEX talks_section ON talks(section_id);
CREATE TABLE talk_participants (
    talk_id uuid REFERENCES talks(id) ON DELETE CASCADE,
    person_id uuid REFERENCES people(id), role text CHECK (role IN ('speaker', 'coauthor')),
    "order" integer NOT NULL CHECK ("order" > 0),
    PRIMARY KEY (talk_id, person_id, role), UNIQUE (talk_id, role, "order")
);
CREATE TABLE reports (
    id uuid PRIMARY KEY, author_id uuid NOT NULL REFERENCES users(id),
    conference_id uuid NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
    template_version text NOT NULL CHECK (length(template_version) > 0), days jsonb NOT NULL,
    version integer NOT NULL CHECK (version > 0), edited_by uuid NOT NULL REFERENCES users(id)
);
CREATE INDEX reports_conference ON reports(conference_id);
CREATE TABLE report_sections (
    report_id uuid REFERENCES reports(id) ON DELETE CASCADE,
    section_id uuid REFERENCES sections(id) DEFERRABLE INITIALLY DEFERRED,
    position integer NOT NULL CHECK (position >= 0),
    PRIMARY KEY (report_id, section_id)
);
CREATE TABLE report_talks (
    report_id uuid REFERENCES reports(id) ON DELETE CASCADE,
    talk_id uuid REFERENCES talks(id) DEFERRABLE INITIALLY DEFERRED,
    position integer NOT NULL CHECK (position >= 0),
    PRIMARY KEY (report_id, talk_id)
);
CREATE TABLE report_versions (
    id uuid PRIMARY KEY, report_id uuid NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    number integer NOT NULL CHECK (number > 0), input jsonb NOT NULL,
    variables jsonb NOT NULL, files jsonb NOT NULL, created_at timestamptz NOT NULL,
    created_by uuid NOT NULL REFERENCES users(id), UNIQUE (report_id, number)
);
