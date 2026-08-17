-- Baculus control plane — Row Level Security posture and role notes.
--
-- Security model (see docs/architecture/M0.md and .env.example):
--   * SUPABASE_SERVICE_ROLE_KEY is server-only and bypasses RLS. It is used by
--     the ingestion engine / CI, and MUST NEVER reach a browser or client
--     bundle.
--   * The publishable (anon) key is the only key that may reach a browser and
--     is governed by the RLS policies below.
--
-- For M0 there is no end-user UI, so the default posture is: RLS ENABLED with
-- NO anon policies (deny-all to anon/authenticated), while the service role —
-- which bypasses RLS entirely — performs all writes. This prevents any future
-- browser client from reading the governance plane by accident.

set search_path = baculus, public;

do $$
declare
    t text;
    tables text[] := array[
        'sources', 'source_datasets', 'ingestion_runs', 'raw_artifacts',
        'raw_observations', 'reference_data_versions', 'dataset_builds',
        'dataset_artifacts', 'validation_runs', 'validation_findings',
        'quarantine_cases', 'quarantine_decisions', 'governance_events',
        'audit_events', 'reconciliations'
    ];
begin
    foreach t in array tables loop
        execute format('alter table baculus.%I enable row level security;', t);
        execute format('alter table baculus.%I force row level security;', t);
    end loop;
end;
$$;

-- No policies are created for anon/authenticated => deny-all to those roles.
-- The service role bypasses RLS and remains the only writer at M0.
--
-- A future read-only UI should add NARROW, column-limited SELECT policies on a
-- vetted subset (e.g. dataset_builds.state) for the authenticated role only —
-- never exposing vendor payloads, request parameters, or secrets.
