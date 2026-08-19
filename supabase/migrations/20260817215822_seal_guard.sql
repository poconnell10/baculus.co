-- Baculus control plane — database-level SEALED guard (defense in depth).
--
-- The authoritative enforcement of the SEALED rule lives in application code
-- (engine/baculus/governance/state_machine.py). This migration ADDS a second,
-- independent guard in the control plane so that a dataset row cannot be flipped
-- to SEALED directly in the database without a passed, independent (M4)
-- reconciliation. There is deliberately no bypass flag.

set search_path = baculus, public;

-- Records of independent reconciliation passes (populated by a future M4).
create table if not exists reconciliations (
    id                uuid primary key default gen_random_uuid(),
    dataset_build_id  uuid not null references dataset_builds(id),
    -- True only when at least one source OTHER THAN the dataset's own source was
    -- cross-checked. Single-source (Massive-only) reconciliations are never
    -- independent and therefore never satisfy the seal guard.
    independent       boolean not null,
    status            text not null check (status in ('PASSED', 'FAILED')),
    sources           jsonb not null,
    evidence_ref      text,
    created_at        timestamptz not null default now()
);

create or replace function baculus.enforce_seal_guard()
returns trigger
language plpgsql
as $$
begin
    if new.state = 'SEALED' and old.state is distinct from 'SEALED' then
        if not exists (
            select 1 from baculus.reconciliations r
            where r.dataset_build_id = new.id
              and r.status = 'PASSED'
              and r.independent = true
        ) then
            raise exception
                'SEALED blocked: dataset build % requires a passed independent (M4) reconciliation',
                new.id
                using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end;
$$;

drop trigger if exists dataset_builds_seal_guard on dataset_builds;
create trigger dataset_builds_seal_guard
    before update on dataset_builds
    for each row execute function baculus.enforce_seal_guard();
