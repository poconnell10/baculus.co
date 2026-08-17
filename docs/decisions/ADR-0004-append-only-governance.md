# ADR-0004: Append-only governance ledgers

- Status: Accepted
- Date: 2024 (M0)

## Context

Governance and audit records must resist after-the-fact rewriting. A quarantine
decision that could be silently edited is not evidence.

## Decision

`audit_events`, `governance_events`, and `quarantine_decisions` are append-only.
INSERT is allowed; UPDATE and DELETE are rejected by database-level triggers
(Postgres `reject_mutation()`; SQLite `RAISE(ABORT)`). A changed quarantine
decision is a **new superseding row** (`supersedes_decision_id`), never an edit.

## Consequences

- The history of governance actions is monotonic and reconstructable.
- We explicitly document this as tamper *resistance*, not immutability: a
  privileged DBA can still disable triggers. Absolute immutability is not
  claimed; the GitHub digest anchor (ADR-0007) provides independent corroboration.
- Application code offers no update/delete paths for these tables.
