-- parcelkey pipeline tables.
-- gnaf_address and property are shared reference data and carry no agency. Every other table has an agency_id that is part of its key
-- and forced row level security (see 003_rls.sql).

-- One flat row per G-NAF address, with the same normalised parts a source record gets. Built from the G-NAF tables by parcelkey.gnaf.build_reference.
CREATE TABLE IF NOT EXISTS gnaf_address (
    address_detail_pid text PRIMARY KEY,
    unit          text NOT NULL DEFAULT '',
    level         text NOT NULL DEFAULT '',
    building      text NOT NULL DEFAULT '',
    lot           text NOT NULL DEFAULT '',
    number_first  text NOT NULL DEFAULT '',
    number_last   text NOT NULL DEFAULT '',
    street_name   text NOT NULL,
    street_type   text NOT NULL DEFAULT '',
    street_key    text NOT NULL,
    locality_key  text NOT NULL,
    state         text NOT NULL,
    postcode      text NOT NULL DEFAULT '',
    canon_key     text NOT NULL,
    geog          geography(Point, 4326)
);
CREATE INDEX IF NOT EXISTS gnaf_address_block_postcode_ix ON gnaf_address (postcode, street_key);
CREATE INDEX IF NOT EXISTS gnaf_address_block_locality_ix ON gnaf_address (locality_key, state, street_key);
CREATE INDEX IF NOT EXISTS gnaf_address_street_trgm_ix ON gnaf_address USING gin (street_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS gnaf_address_geog_ix ON gnaf_address USING gist (geog);
CREATE INDEX IF NOT EXISTS gnaf_address_canon_ix ON gnaf_address (canon_key);

-- The master property list. property_id is derived, never assigned from a sequence: from the G-NAF pid when a record matched one, else from the canonical key.
CREATE TABLE IF NOT EXISTS property (
    property_id    text PRIMARY KEY,
    origin         text NOT NULL CHECK (origin IN ('gnaf', 'derived')),
    gnaf_pid       text UNIQUE,
    canon_key      text NOT NULL,
    display_address text NOT NULL,
    postcode       text NOT NULL DEFAULT '',
    state          text NOT NULL DEFAULT '',
    geog           geography(Point, 4326),
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK ((origin = 'gnaf' AND gnaf_pid IS NOT NULL) OR (origin = 'derived' AND gnaf_pid IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS property_derived_key_ux ON property (canon_key) WHERE origin = 'derived';

CREATE TABLE IF NOT EXISTS agency (
    agency_id  text PRIMARY KEY CHECK (agency_id <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_load (
    agency_id   text        NOT NULL CHECK (agency_id <> ''),
    load_id     bigserial,
    source      text        NOT NULL,
    file_name   text        NOT NULL,
    file_sha256 text        NOT NULL,
    row_count   integer     NOT NULL,
    stored      integer     NOT NULL,
    rejected    integer     NOT NULL,
    unchanged   integer     NOT NULL DEFAULT 0,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, load_id),
    UNIQUE (agency_id, source, file_sha256)
);

CREATE TABLE IF NOT EXISTS source_record (
    agency_id   text        NOT NULL CHECK (agency_id <> ''),
    record_id   text        NOT NULL,
    source      text        NOT NULL,
    source_ref  text        NOT NULL,
    load_id     bigint      NOT NULL,
    line_no     integer     NOT NULL,
    raw         jsonb       NOT NULL,
    raw_sha256  text        NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    norm_status text        NOT NULL DEFAULT 'pending' CHECK (norm_status IN ('pending', 'ok', 'unparseable')),
    norm_rules  text[]      NOT NULL DEFAULT '{}',
    unit        text        NOT NULL DEFAULT '',
    level       text        NOT NULL DEFAULT '',
    building    text        NOT NULL DEFAULT '',
    lot         text        NOT NULL DEFAULT '',
    number_first text       NOT NULL DEFAULT '',
    number_last text        NOT NULL DEFAULT '',
    street_name text        NOT NULL DEFAULT '',
    street_type text        NOT NULL DEFAULT '',
    street_key  text        NOT NULL DEFAULT '',
    locality_key text       NOT NULL DEFAULT '',
    state       text        NOT NULL DEFAULT '',
    postcode    text        NOT NULL DEFAULT '',
    canon_key   text        NOT NULL DEFAULT '',
    geog        geography(Point, 4326),
    contact_name  text      NOT NULL DEFAULT '',
    contact_email text      NOT NULL DEFAULT '',
    contact_phone text      NOT NULL DEFAULT '',
    contact_rules text[]    NOT NULL DEFAULT '{}',
    PRIMARY KEY (agency_id, record_id),
    UNIQUE (agency_id, source, source_ref),
    FOREIGN KEY (agency_id, load_id) REFERENCES source_load (agency_id, load_id)
);

CREATE TABLE IF NOT EXISTS ingest_reject (
    agency_id   text    NOT NULL CHECK (agency_id <> ''),
    reject_id   bigserial,
    source      text    NOT NULL,
    file_sha256 text    NOT NULL,
    line_no     integer NOT NULL,
    reason_code text    NOT NULL,
    reason      text    NOT NULL,
    raw         jsonb   NOT NULL,
    PRIMARY KEY (agency_id, reject_id),
    UNIQUE (agency_id, source, file_sha256, line_no)
);

-- One row per decided source record: which property, by which rule, with what score. Never rewritten by a rerun.
CREATE TABLE IF NOT EXISTS property_link (
    agency_id   text        NOT NULL CHECK (agency_id <> ''),
    record_id   text        NOT NULL,
    property_id text        NOT NULL REFERENCES property (property_id),
    rule        text        NOT NULL,
    rules       text[]      NOT NULL DEFAULT '{}',
    score       numeric(5,4) NOT NULL CHECK (score >= 0 AND score <= 1),
    decided_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, record_id),
    FOREIGN KEY (agency_id, record_id) REFERENCES source_record (agency_id, record_id)
);
CREATE INDEX IF NOT EXISTS property_link_property_ix ON property_link (agency_id, property_id);

-- A record set aside with the reason. It stays here until a later run can link it, then its status becomes resolved.
CREATE TABLE IF NOT EXISTS quarantine (
    agency_id   text        NOT NULL CHECK (agency_id <> ''),
    record_id   text        NOT NULL,
    reason_code text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}',
    status      text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    PRIMARY KEY (agency_id, record_id),
    FOREIGN KEY (agency_id, record_id) REFERENCES source_record (agency_id, record_id)
);

CREATE TABLE IF NOT EXISTS contact (
    agency_id    text        NOT NULL CHECK (agency_id <> ''),
    contact_id   text        NOT NULL,
    display_name text        NOT NULL DEFAULT '',
    merged_into  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, contact_id)
);

-- An email or an E.164 phone belongs to exactly one contact within an agency.
CREATE TABLE IF NOT EXISTS contact_identifier (
    agency_id  text NOT NULL CHECK (agency_id <> ''),
    kind       text NOT NULL CHECK (kind IN ('email', 'phone')),
    value      text NOT NULL,
    contact_id text NOT NULL,
    PRIMARY KEY (agency_id, kind, value),
    FOREIGN KEY (agency_id, contact_id) REFERENCES contact (agency_id, contact_id)
);

CREATE TABLE IF NOT EXISTS contact_link (
    agency_id  text        NOT NULL CHECK (agency_id <> ''),
    record_id  text        NOT NULL,
    contact_id text        NOT NULL,
    rule       text        NOT NULL,
    evidence   jsonb       NOT NULL DEFAULT '{}',
    decided_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, record_id),
    FOREIGN KEY (agency_id, record_id) REFERENCES source_record (agency_id, record_id),
    FOREIGN KEY (agency_id, contact_id) REFERENCES contact (agency_id, contact_id)
);

CREATE TABLE IF NOT EXISTS contact_merge (
    agency_id text        NOT NULL CHECK (agency_id <> ''),
    merge_id  bigserial,
    loser     text        NOT NULL,
    survivor  text        NOT NULL,
    evidence  jsonb       NOT NULL DEFAULT '{}',
    merged_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, merge_id)
);

-- A contact mention that was not resolved to a contact, with the reason (a cluster that would grow too large).
CREATE TABLE IF NOT EXISTS contact_quarantine (
    agency_id   text        NOT NULL CHECK (agency_id <> ''),
    record_id   text        NOT NULL,
    reason_code text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, record_id),
    FOREIGN KEY (agency_id, record_id) REFERENCES source_record (agency_id, record_id)
);

-- Listing campaigns and contacts as seen from the master property. security_invoker makes them obey the caller's row level security.
CREATE OR REPLACE VIEW campaign_property WITH (security_invoker = true) AS
    SELECT l.agency_id, r.raw ->> 'campaign_id' AS campaign_id, l.property_id, l.record_id
    FROM property_link l JOIN source_record r ON r.agency_id = l.agency_id AND r.record_id = l.record_id
    WHERE r.source = 'listing' AND coalesce(r.raw ->> 'campaign_id', '') <> '';

CREATE OR REPLACE VIEW contact_property WITH (security_invoker = true) AS
    SELECT c.agency_id, c.contact_id, l.property_id, c.record_id, r.source AS relationship
    FROM contact_link c
    JOIN property_link l ON l.agency_id = c.agency_id AND l.record_id = c.record_id
    JOIN source_record r ON r.agency_id = c.agency_id AND r.record_id = c.record_id;
