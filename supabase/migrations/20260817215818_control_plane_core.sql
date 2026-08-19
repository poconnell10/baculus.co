-- Baculus control plane — core ingestion/provenance tables.
--
-- Postgres is the governance/metadata plane ONLY. Large historical market-data
-- bodies are NEVER stored here; raw and canonical artifacts live in object
-- storage (Supabase Storage). This migration mirrors the SQLite dev schema in
-- engine/baculus/governance/schema_sqlite.sql.

create schema if not exists baculus;
set search_path = baculus, public;

-- Sources & datasets ---------------------------------------------------------
create table if not exists sources (
    id          text primary key,
    name        text not null,
    kind        text not null,
    -- Whether this source may, on its own, back a SEALED dataset. Always false
    -- for a single vendor: SEALED requires an independent M4 reconciliation.
    authoritative boolean not null default false,
    created_at  timestamptz not null default now()
);

create table if not exists source_datasets (
    id          uuid primary key default gen_random_uuid(),
    source_id   text not null references sources(id),
    dataset     text not null,
    description text,
    created_at  timestamptz not null default now(),
    unique (source_id, dataset)
);

-- Ingestion runs -------------------------------------------------------------
create table if not exists ingestion_runs (
    id                 uuid primary key default gen_random_uuid(),
    source_id          text not null references sources(id),
    dataset            text not null,
    request_parameters jsonb not null,
    fetch_mode         text not null check (fetch_mode in ('live', 'fixture')),
    observation_time   timestamptz not null,
    status             text not null,
    created_at         timestamptz not null default now()
);

-- Raw artifacts (content-addressed) -----------------------------------------
-- One row per DISTINCT content; id == sha256. Rows are never overwritten.
create table if not exists raw_artifacts (
    id              text primary key,          -- = sha256
    source_id       text not null references sources(id),
    dataset         text not null,
    sha256          text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    object_path     text not null,
    byte_size       bigint not null,
    content_type    text not null,
    file_extension  text not null,
    event_date_min  date not null,
    event_date_max  date not null,
    row_count       bigint,
    schema_version  text not null,
    logical_key     text not null,
    vintage         integer not null,
    fetch_mode      text not null check (fetch_mode in ('live', 'fixture')),
    created_at      timestamptz not null default now(),
    unique (source_id, sha256),
    unique (logical_key, vintage)
);
create index if not exists raw_artifacts_logical_key_idx on raw_artifacts (logical_key);

-- Raw observations: one row per time an artifact was OBSERVED (per run).
create table if not exists raw_observations (
    id               uuid primary key default gen_random_uuid(),
    run_id           uuid not null references ingestion_runs(id),
    artifact_id      text not null references raw_artifacts(id),
    source_id        text not null references sources(id),
    dataset          text not null,
    logical_key      text not null,
    observation_time timestamptz not null,
    is_new_content   boolean not null,
    created_at       timestamptz not null default now()
);
create index if not exists raw_observations_logical_key_idx on raw_observations (logical_key);

-- Reference data versions (e.g. trading calendar) ---------------------------
create table if not exists reference_data_versions (
    id          uuid primary key default gen_random_uuid(),
    kind        text not null,
    provider    text not null,
    name        text not null,
    version     text not null,
    vintage     text not null,
    descriptor  jsonb not null,
    created_at  timestamptz not null default now(),
    unique (kind, name, version)
);
