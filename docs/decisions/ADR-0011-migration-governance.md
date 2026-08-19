# ADR-0011: Migration governance — one history, two replay mechanisms

- Status: **Accepted** (repository preparation; first remote push pending CTO approval).
- Date: 2026-08 (M0 remote initialization stage).

## Context

M0's control-plane schema is defined by exactly one ordered set of SQL files
under `supabase/migrations/`. Before the first persistent remote database
(`baculus-dev`) is initialized, we must fix a governance question: what is the
**authoritative** mechanism for migrating persistent remote environments, and how
do local/CI runs relate to it — so we never end up with a manually-applied remote
schema whose state diverges from Supabase's migration tracking.

Two facts shaped the decision:

- The migration files were originally named `0001_*.sql … 0007_*.sql`
  (integer-sequenced), which is **not** the Supabase CLI's 14-digit timestamp
  convention. Whether `supabase db push` accepts non-timestamp prefixes is
  CLI-version-dependent, so relying on it is fragile.
- Nothing in the repo tracked migrations via Supabase's
  `supabase_migrations.schema_migrations`. The de facto authority was the ordered
  files, replayed by `apply_migrations()` and by the CI psql loop. The remote was
  still empty — the zero-cost moment to establish governance.

## Decision

**One migration history; the mechanism that is authoritative depends on the
target.**

### Persistent remote Supabase environments (e.g. `baculus-dev`, future prod)
- Migrations use the **Supabase timestamp convention** (`<YYYYMMDDHHMMSS>_name.sql`).
- Persistent remote databases are migrated **only** through the approved Supabase
  CLI workflow (`supabase db push`), and their state is tracked in
  `supabase_migrations.schema_migrations`.
- **Prohibited** against a tracked remote: `apply_migrations()`, `psql -f`, or any
  direct execution of migration SQL. These do not update `schema_migrations` and
  would reintroduce drift.
- Remote migration state must remain consistent with
  `supabase_migrations.schema_migrations`.

### Local / CI ephemeral (vanilla Postgres)
- May replay the exact repository migration files directly from zero via the
  ordered runner (`apply_migrations()`) or the CI psql loop.
- Exist for reproducibility and testing only. They **do not** establish, track, or
  reconcile remote migration state.
- This independent, tool-agnostic replay is deliberately retained: it proves the
  same files construct M0 from an empty database without the Supabase CLI.

### Immutability
- **No existing migration may be edited after it has been applied to a persistent
  environment.** All subsequent schema changes require a **new** migration
  (`supabase migration new <name>`), applied via `supabase db push`.

## Consequences

- The seven migrations were renamed (pure `git mv`, contents byte-identical) from
  `000N_*` to timestamped names anchored to their git-add times. Lexical order is
  unchanged (1→7), so `apply_migrations()` (a `sorted(glob)`) and the CI glob loop
  behave identically.
- `supabase/config.toml` is committed (non-secret) so the CLI treats the repo as a
  Supabase project. The project ref comes from `supabase link` (gitignored
  `supabase/.temp/`), never from the repo.
- CI keeps its ephemeral-Postgres psql-loop job unchanged: it is an *independent*
  from-zero replay proof and is not a remote-tracking mechanism. It is not switched
  to `supabase db push`.
- Reproducibility: a clean environment is built by `supabase db push` (remote) or
  `apply_migrations` / `supabase db reset` (local); all share the same files, so
  the resulting schema is identical (verified by an object-level schema
  fingerprint).
