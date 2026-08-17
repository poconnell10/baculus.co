# ADR-0007: Deterministic manifests and the secondary GitHub anchor

- Status: Accepted
- Date: 2024 (M0)

## Context

Tamper evidence needs a stable fingerprint of governed content and a witness in
an independent trust domain.

## Decision

Serialize manifests canonically (sorted keys, ISO dates/datetimes, no
insignificant whitespace, fail-closed on unknown types) so the same logical
content always yields the same SHA-256 digest. Periodically commit that digest
into GitHub — a separate provider/trust domain — as a **secondary
tamper-evidence anchor** (an append-only ledger intended to be committed).

## Consequences

- Detecting tampering requires compromising both the control plane and GitHub.
- We explicitly do **not** claim Git history is immutable (force-push/rewrite is
  possible); the anchor provides corroboration, not proof of immutability.
- Determinism is testable: identical content ⇒ identical digest; any change ⇒ a
  different digest.
