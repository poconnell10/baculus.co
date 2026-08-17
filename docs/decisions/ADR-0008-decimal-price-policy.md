# ADR-0008: Decimal price representation

- Status: Accepted
- Date: 2024 (M0 integration)

## Context

Binary floating point cannot represent most decimal money values exactly.
Reconstructing decades of split/dividend adjustments multiplies and divides
prices repeatedly; float rounding error would accumulate and, worse, be
non-reproducible across platforms. We must not discover these differences later.

## Decision

Prices are **fixed-precision `Decimal`** at the canonical and persisted
boundaries — never binary float.

- Canonical policy: `Decimal(precision=18, scale=8)` (`PRICE_DTYPE` in
  `engine/baculus/models/bar.py`). Eight fractional digits represent a 1e-8
  minimum tick with headroom for adjusted-price reconstruction.
- Ingestion parses the vendor's **textual** number directly into `Decimal`
  (`json.loads(..., parse_float=Decimal)`), so no value passes through float.
- Values with more fractional digits than the scale are **rejected** (fail
  closed), never silently rounded.
- Persistence: Polars `Decimal` → Parquet `decimal128`, which round-trips
  exactly. Postgres stores no price bodies (control plane only); when prices
  reach relational storage in a later milestone they use `numeric`.
- Volume remains an integer count of shares.

## Consequences

- Round-trip is exact and reproducible: vendor text → `Decimal` → Parquet →
  `Decimal` (proven in `engine/tests/test_decimal_prices.py`).
- Canonical JSON serializes `Decimal` to a normalized string, so equal values
  hash equally regardless of trailing-zero scale.
- Adjustment math in a later milestone operates on `Decimal`/`numeric`, keeping
  reconstruction deterministic.
- Slightly more care at the vendor boundary (textual parse, over-precision
  rejection), accepted as foundational for a research platform.
