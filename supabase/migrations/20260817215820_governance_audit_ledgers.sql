-- Baculus control plane — append-only governance & audit ledgers.

set search_path = baculus, public;

create table if not exists governance_events (
    id           uuid primary key default gen_random_uuid(),
    event_type   text not null,
    severity     text not null check (severity in ('INFO', 'WARNING', 'ERROR')),
    subject_type text not null,
    subject_id   text,
    run_id       uuid,
    payload      jsonb not null,
    created_at   timestamptz not null default now()
);

create table if not exists audit_events (
    id           uuid primary key default gen_random_uuid(),
    actor        text not null,
    action       text not null,
    subject_type text not null,
    subject_id   text,
    payload      jsonb not null,
    created_at   timestamptz not null default now()
);
