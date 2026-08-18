# ADR-0010: Raw artifact identity vs logical market-data identity

- Status: Accepted
- Date: 2024 (M0 provenance correction)

## Context

Restatement detection originally keyed solely on raw-byte (SHA-256) inequality
for a logical range. But vendor responses carry volatile non-market metadata —
request ids, generated/retrieval timestamps, trace ids, pagination and transport
metadata — that can change between requests while the underlying market
observations are identical. Keying restatement on raw bytes alone would
manufacture false restatements and false economic vintages from metadata noise.

(This was made concrete by the proof harness itself: isolating proof runs with a
per-run `request_id` produced different raw bytes for identical market data.)

## Decision

Track two distinct identities per raw artifact:

- **`raw_sha256`** — SHA-256 of the exact vendor bytes. Immutable raw provenance
  and the content address for storage. Changes on any byte change.
- **`logical_data_sha256`** — deterministic hash of the canonical market
  *observations* only (`ECONOMIC_BAR_FIELDS`: symbol, event_date, OHLC, volume),
  excluding observation time, artifact linkage, and all vendor/transport
  metadata. Computed in `baculus.lineage.identity`.

Classification of a newly-fetched artifact for a logical key:

| raw bytes | logical data | outcome |
| --- | --- | --- |
| same | same | **identical refetch** — no new artifact; audit/run only |
| different | same | **raw-only change** — new raw artifact/observation for exact provenance; **not** a restatement; **no** new economic vintage; recorded as an `audit_event` (`raw_metadata_changed_same_logical`), not governance |
| different | different | **restatement** — retain both, new economic vintage, preserve prior, emit `RESTATEMENT_DETECTED` identifying exactly which observations changed |

Economic `vintage` is allocated per distinct `logical_data_sha256` within a
logical key (`resolve_economic_vintage`). Multiple raw artifacts may therefore
share one economic vintage, so the old `unique(logical_key, vintage)` constraint
is dropped (migration `0007`); raw identity stays unique via
`unique(source_id, sha256)`.

## Consequences

- Restatement is an *economic* event, not a byte-diff artifact. Volatile vendor
  metadata never fabricates a vintage.
- Exact raw provenance is still preserved: every distinct raw response is stored
  and content-addressed, even when logically redundant.
- Restatement events name the exact changed `(symbol, event_date)` observations.
- Tests enforce both directions: *same bars + different request id → no
  restatement*, and *changed bar (+ any request id) → restatement*. The
  real-stack proof (`verify_infra.py`) asserts the raw-only case too.
- A proof-run isolation nonce is legitimate **only** because it is classified as
  volatile metadata and excluded from logical hashing.
