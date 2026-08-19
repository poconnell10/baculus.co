-- Baculus control plane — dataset builds, validation, quarantine.

set search_path = baculus, public;

-- Dataset builds & artifacts -------------------------------------------------
create table if not exists dataset_builds (
    id              uuid primary key default gen_random_uuid(),
    source_id       text not null references sources(id),
    dataset         text not null,
    logical_key     text not null,
    vintage         integer not null,
    -- DatasetState. SEALED is unreachable for single-source data (enforced in
    -- application code + guarded here by documentation; see 0004 trigger note).
    state           text not null check (
        state in ('RAW', 'VALIDATED', 'PROVISIONAL', 'QUARANTINED', 'SEALED', 'SUPERSEDED')
    ),
    manifest_digest text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create table if not exists dataset_artifacts (
    id               uuid primary key default gen_random_uuid(),
    dataset_build_id uuid not null references dataset_builds(id),
    kind             text not null,           -- 'canonical_parquet' | 'manifest'
    object_path      text not null,
    sha256           text not null,
    byte_size        bigint not null,
    created_at       timestamptz not null default now()
);

-- Validation -----------------------------------------------------------------
create table if not exists validation_runs (
    id               uuid primary key default gen_random_uuid(),
    dataset_build_id uuid references dataset_builds(id),
    artifact_id      text references raw_artifacts(id),
    ruleset_version  text not null,
    status           text not null check (status in ('PASSED', 'FAILED')),
    error_count      integer not null,
    warning_count    integer not null,
    created_at       timestamptz not null default now()
);

create table if not exists validation_findings (
    id                uuid primary key default gen_random_uuid(),
    validation_run_id uuid not null references validation_runs(id),
    rule_id           text not null,
    rule_version      text not null,
    severity          text not null check (severity in ('INFO', 'WARNING', 'ERROR')),
    source            text,
    artifact_id       text,
    symbol            text,
    event_date        date,
    observed_value    text,
    reason            text not null,
    created_at        timestamptz not null default now()
);

-- Quarantine -----------------------------------------------------------------
create table if not exists quarantine_cases (
    id          uuid primary key default gen_random_uuid(),
    artifact_id text references raw_artifacts(id),
    dataset     text not null,
    reason      text not null,
    status      text not null check (status in ('OPEN', 'RESOLVED')),
    created_at  timestamptz not null default now()
);

-- Append-only. A changed decision is a NEW superseding row (see 0003 triggers).
create table if not exists quarantine_decisions (
    id                      uuid primary key default gen_random_uuid(),
    case_id                 uuid not null references quarantine_cases(id),
    actor                   text not null,
    decided_at              timestamptz not null,
    reason                  text not null,
    evidence_ref            text,
    disposition             text not null,
    triggering_rule_version text,
    supersedes_decision_id  uuid references quarantine_decisions(id),
    created_at              timestamptz not null default now()
);
