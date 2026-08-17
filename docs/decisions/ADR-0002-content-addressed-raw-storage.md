# ADR-0002: Content-addressed raw storage with vintaging

- Status: Accepted
- Date: 2024 (M0)

## Context

Raw vendor payloads must be retained verbatim and never overwritten, while
identical refetches must not duplicate market-data content.

## Decision

Store raw payloads by the SHA-256 of their bytes at
`raw/<source>/<dataset>/observation_date=YYYY-MM-DD/<sha256>.<ext>`. Storage is
idempotent: identical bytes resolve to the same object and are never rewritten;
different bytes for the same logical range land at a new path and create a new
**vintage**. The control plane records one `raw_artifact` per distinct content
and one `raw_observation` per time an artifact was observed.

## Consequences

- Idempotency and history-preservation fall out of the addressing scheme for
  free; the runner only decides whether content is new.
- Bodies live in object storage, not Postgres, keeping the control plane small.
- Content addressing detects restatements deterministically (hash mismatch),
  which drives `RESTATEMENT_DETECTED` / `NEW_VINTAGE_CREATED` events.
