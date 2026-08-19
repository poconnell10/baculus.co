-- Baculus control plane — append-only enforcement via triggers.
--
-- audit_events, governance_events and quarantine_decisions are append-only:
-- INSERT is allowed (subject to role/RLS), UPDATE and DELETE are rejected at
-- the database level. A changed quarantine decision must be a NEW superseding
-- row (supersedes_decision_id), never an edit of the original.
--
-- NOTE ON TAMPER RESISTANCE: these triggers provide tamper *resistance*, not
-- absolute immutability. A sufficiently privileged database administrator can
-- disable triggers or alter tables. Tamper *evidence* is provided additionally
-- by the deterministic manifest digest anchored into GitHub (a separate trust
-- domain) — see docs/architecture/M0.md.

set search_path = baculus, public;

create or replace function baculus.reject_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception '% is append-only: % denied', tg_table_name, tg_op
        using errcode = 'restrict_violation';
end;
$$;

-- audit_events
drop trigger if exists audit_events_no_update on audit_events;
create trigger audit_events_no_update
    before update on audit_events
    for each row execute function baculus.reject_mutation();

drop trigger if exists audit_events_no_delete on audit_events;
create trigger audit_events_no_delete
    before delete on audit_events
    for each row execute function baculus.reject_mutation();

-- governance_events
drop trigger if exists governance_events_no_update on governance_events;
create trigger governance_events_no_update
    before update on governance_events
    for each row execute function baculus.reject_mutation();

drop trigger if exists governance_events_no_delete on governance_events;
create trigger governance_events_no_delete
    before delete on governance_events
    for each row execute function baculus.reject_mutation();

-- quarantine_decisions
drop trigger if exists quarantine_decisions_no_update on quarantine_decisions;
create trigger quarantine_decisions_no_update
    before update on quarantine_decisions
    for each row execute function baculus.reject_mutation();

drop trigger if exists quarantine_decisions_no_delete on quarantine_decisions;
create trigger quarantine_decisions_no_delete
    before delete on quarantine_decisions
    for each row execute function baculus.reject_mutation();
