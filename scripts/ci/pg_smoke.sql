-- Postgres smoke test: proves the migrations' governance invariants actually
-- fire in Postgres (not just in the SQLite dev mirror). Run with:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/ci/pg_smoke.sql
-- Any DO block that detects a missing guard raises, failing the CI step.

set search_path = baculus, public;

-- 1) audit_events is append-only: UPDATE must be rejected.
do $$
declare rejected boolean := false;
begin
    insert into baculus.audit_events(actor, action, subject_type, subject_id, payload)
        values ('ci', 'smoke', 'test', '1', '{}'::jsonb);
    begin
        update baculus.audit_events set actor = 'tamper';
    exception when others then
        rejected := true;  -- expected: append-only trigger fired
    end;
    if not rejected then
        raise exception 'FAIL: audit_events UPDATE was not rejected';
    end if;
end $$;

-- 2) audit_events is append-only: DELETE must be rejected.
do $$
declare rejected boolean := false;
begin
    begin
        delete from baculus.audit_events;
    exception when others then
        rejected := true;
    end;
    if not rejected then
        raise exception 'FAIL: audit_events DELETE was not rejected';
    end if;
end $$;

-- 3) governance_events is append-only: UPDATE must be rejected.
do $$
declare rejected boolean := false;
begin
    insert into baculus.governance_events(event_type, severity, subject_type, subject_id, payload)
        values ('SMOKE', 'INFO', 'test', '1', '{}'::jsonb);
    begin
        update baculus.governance_events set severity = 'ERROR';
    exception when others then
        rejected := true;
    end;
    if not rejected then
        raise exception 'FAIL: governance_events UPDATE was not rejected';
    end if;
end $$;

-- 4) SEALED guard: a single-source PROVISIONAL build cannot be flipped to SEALED
--    without a passed, independent (M4) reconciliation.
do $$
declare rejected boolean := false; bid uuid;
begin
    insert into baculus.sources(id, name, kind, authoritative)
        values ('massive', 'Massive', 'market_data_vendor', false)
        on conflict (id) do nothing;
    insert into baculus.dataset_builds(source_id, dataset, logical_key, vintage, state)
        values ('massive', 'stocks/daily', 'massive|stocks/daily|x|y', 1, 'PROVISIONAL')
        returning id into bid;
    begin
        update baculus.dataset_builds set state = 'SEALED' where id = bid;
    exception when others then
        rejected := true;  -- expected: seal guard fired
    end;
    if not rejected then
        raise exception 'FAIL: SEALED guard did not block a single-source seal';
    end if;
end $$;

-- 5) SEALED guard opens only with a passed, independent reconciliation.
do $$
declare bid uuid; final_state text;
begin
    insert into baculus.dataset_builds(source_id, dataset, logical_key, vintage, state)
        values ('massive', 'stocks/daily', 'massive|stocks/daily|x|y', 2, 'PROVISIONAL')
        returning id into bid;
    insert into baculus.reconciliations(dataset_build_id, independent, status, sources)
        values (bid, true, 'PASSED', '["massive","sourceB"]'::jsonb);
    update baculus.dataset_builds set state = 'SEALED' where id = bid;
    select state into final_state from baculus.dataset_builds where id = bid;
    if final_state <> 'SEALED' then
        raise exception 'FAIL: independent M4 reconciliation did not permit SEALED';
    end if;
end $$;

select 'pg_smoke: all governance guards verified' as result;
