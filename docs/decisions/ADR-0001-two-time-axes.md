# ADR-0001: Preserve two time axes (event time vs observation time)

- Status: Accepted
- Date: 2024 (M0)

## Context

Point-in-time research is only trustworthy if the system can reproduce exactly
what was known at a past instant. Vendors restate history; an "as-of-today" view
silently rewrites the past and destroys reproducibility.

## Decision

Every observation carries two independent time axes:

- **event time** (`event_date`) — when the market event happened;
- **observation time** (`observation_time`/`retrieved_at`) — when Baculus
  received that version.

These are never collapsed. Canonical bars also carry `source_artifact_id` (the
raw SHA-256), tying every value back to the exact artifact/vintage it came from.

## Consequences

- We can answer "what did we know at time T?" by resolving the vintage current
  at that observation time.
- Storage and the control plane must key on both axes; a single `event_date`
  primary key is forbidden because it cannot represent restatements.
- Slightly more storage and schema complexity, accepted as foundational.
