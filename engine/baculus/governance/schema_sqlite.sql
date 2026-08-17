-- Baculus control-plane schema (SQLite mirror of supabase/migrations).
--
-- This is the governance/metadata plane. It must NOT store large historical
-- market-data bodies; those live in object storage. Append-only ledgers
-- (audit_events, governance_events, quarantine_decisions) are protected by
-- triggers that reject UPDATE and DELETE.

PRAGMA foreign_keys = ON;

-- Sources & datasets ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- e.g. 'market_data_vendor'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_datasets (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES sources(id),
    dataset     TEXT NOT NULL,            -- vendor-neutral logical id
    description TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (source_id, dataset)
);

-- Ingestion runs -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                 TEXT PRIMARY KEY,
    source_id          TEXT NOT NULL REFERENCES sources(id),
    dataset            TEXT NOT NULL,
    request_parameters TEXT NOT NULL,     -- canonical JSON
    fetch_mode         TEXT NOT NULL,     -- 'live' | 'fixture'
    observation_time   TEXT NOT NULL,     -- when this version was retrieved
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

-- Raw artifacts (content-addressed) -----------------------------------------
-- One row per DISTINCT content. id == sha256. Never overwritten.
CREATE TABLE IF NOT EXISTS raw_artifacts (
    id              TEXT PRIMARY KEY,     -- = sha256
    source_id       TEXT NOT NULL REFERENCES sources(id),
    dataset         TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    object_path     TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    content_type    TEXT NOT NULL,
    file_extension  TEXT NOT NULL,
    event_date_min  TEXT NOT NULL,
    event_date_max  TEXT NOT NULL,
    row_count       INTEGER,
    schema_version  TEXT NOT NULL,
    logical_key     TEXT NOT NULL,        -- source|dataset|emin|emax
    vintage         INTEGER NOT NULL,     -- 1-based, per logical_key
    fetch_mode      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (source_id, sha256)
);

-- Raw observations: one row per time we OBSERVED an artifact (per run).
-- Identical refetch => new observation row pointing at the SAME artifact.
CREATE TABLE IF NOT EXISTS raw_observations (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES ingestion_runs(id),
    artifact_id      TEXT NOT NULL REFERENCES raw_artifacts(id),
    source_id        TEXT NOT NULL REFERENCES sources(id),
    dataset          TEXT NOT NULL,
    logical_key      TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    is_new_content   INTEGER NOT NULL,    -- 1 if this obs created a new artifact
    created_at       TEXT NOT NULL
);

-- Reference data versions (e.g. trading calendar) ---------------------------
CREATE TABLE IF NOT EXISTS reference_data_versions (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,            -- 'trading_calendar'
    provider    TEXT NOT NULL,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    vintage     TEXT NOT NULL,
    descriptor  TEXT NOT NULL,            -- canonical JSON
    created_at  TEXT NOT NULL,
    UNIQUE (kind, name, version)
);

-- Dataset builds & artifacts -------------------------------------------------
CREATE TABLE IF NOT EXISTS dataset_builds (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id),
    dataset         TEXT NOT NULL,
    logical_key     TEXT NOT NULL,
    vintage         INTEGER NOT NULL,
    state           TEXT NOT NULL,        -- DatasetState
    manifest_digest TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_artifacts (
    id               TEXT PRIMARY KEY,
    dataset_build_id TEXT NOT NULL REFERENCES dataset_builds(id),
    kind             TEXT NOT NULL,       -- 'canonical_parquet' | 'manifest'
    object_path      TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    byte_size        INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);

-- Validation -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_runs (
    id               TEXT PRIMARY KEY,
    dataset_build_id TEXT REFERENCES dataset_builds(id),
    artifact_id      TEXT REFERENCES raw_artifacts(id),
    ruleset_version  TEXT NOT NULL,
    status           TEXT NOT NULL,       -- 'PASSED' | 'FAILED'
    error_count      INTEGER NOT NULL,
    warning_count    INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_findings (
    id                TEXT PRIMARY KEY,
    validation_run_id TEXT NOT NULL REFERENCES validation_runs(id),
    rule_id           TEXT NOT NULL,
    rule_version      TEXT NOT NULL,
    severity          TEXT NOT NULL,
    source            TEXT,
    artifact_id       TEXT,
    symbol            TEXT,
    event_date        TEXT,
    observed_value    TEXT,
    reason            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

-- Quarantine -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quarantine_cases (
    id          TEXT PRIMARY KEY,
    artifact_id TEXT REFERENCES raw_artifacts(id),
    dataset     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,            -- 'OPEN' | 'RESOLVED'
    created_at  TEXT NOT NULL
);

-- Append-only. A changed decision is a NEW superseding row, never an edit.
CREATE TABLE IF NOT EXISTS quarantine_decisions (
    id                     TEXT PRIMARY KEY,
    case_id                TEXT NOT NULL REFERENCES quarantine_cases(id),
    actor                  TEXT NOT NULL,
    decided_at             TEXT NOT NULL,
    reason                 TEXT NOT NULL,
    evidence_ref           TEXT,
    disposition            TEXT NOT NULL,
    triggering_rule_version TEXT,
    supersedes_decision_id TEXT REFERENCES quarantine_decisions(id),
    created_at             TEXT NOT NULL
);

-- Governance & audit ledgers (append-only) ----------------------------------
CREATE TABLE IF NOT EXISTS governance_events (
    id           TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id   TEXT,
    run_id       TEXT,
    payload      TEXT NOT NULL,           -- canonical JSON
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id           TEXT PRIMARY KEY,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id   TEXT,
    payload      TEXT NOT NULL,           -- canonical JSON
    created_at   TEXT NOT NULL
);

-- Append-only enforcement: reject UPDATE and DELETE at the database level.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE denied'); END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: DELETE denied'); END;

CREATE TRIGGER IF NOT EXISTS governance_events_no_update
BEFORE UPDATE ON governance_events
BEGIN SELECT RAISE(ABORT, 'governance_events is append-only: UPDATE denied'); END;

CREATE TRIGGER IF NOT EXISTS governance_events_no_delete
BEFORE DELETE ON governance_events
BEGIN SELECT RAISE(ABORT, 'governance_events is append-only: DELETE denied'); END;

CREATE TRIGGER IF NOT EXISTS quarantine_decisions_no_update
BEFORE UPDATE ON quarantine_decisions
BEGIN SELECT RAISE(ABORT, 'quarantine_decisions is append-only: UPDATE denied'); END;

CREATE TRIGGER IF NOT EXISTS quarantine_decisions_no_delete
BEFORE DELETE ON quarantine_decisions
BEGIN SELECT RAISE(ABORT, 'quarantine_decisions is append-only: DELETE denied'); END;
