# ADR-0003: Dataset state machine and the SEALED guard

- Status: Accepted
- Date: 2024 (M0)

## Context

Datasets have a lifecycle, and the most consequential promotion — to `SEALED` —
must not be reachable for data that has not been independently verified.
Single-vendor data has not been.

## Decision

Model explicit states (`RAW`, `VALIDATED`, `PROVISIONAL`, `QUARANTINED`,
`SEALED`, `SUPERSEDED`) with an explicit transition table. `PROVISIONAL →
SEALED` requires `ReconciliationEvidence` that is both **passed** and
**independent** (contains a source other than the dataset's own). A single-source
dataset cannot construct such evidence. No override exists.

Enforced twice: in application code (`assert_transition_allowed`) and by a
database trigger on `dataset_builds` keyed to a `reconciliations` table.

## Consequences

- Massive-only data settles at `PROVISIONAL` and can never be sealed.
- The guard is defense-in-depth: bypassing it requires compromising both code
  and database.
- A future M4 populates `reconciliations`; only then can `SEALED` be reached.
