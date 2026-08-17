# ADR-0005: Vendor-neutral market-data source boundary

- Status: Accepted
- Date: 2024 (M0)

## Context

Baculus must not become coupled to Massive. Adding a future Source B must not
require touching validation or dataset-building logic.

## Decision

Define `MarketDataSourceAdapter` with narrow responsibilities: identify the
source, fetch a requested range, return the verbatim vendor artifact, expose
log-safe request metadata, and normalize its own format into canonical bars.
Explicit non-responsibilities: no strategy logic, no authority decisions, no
idempotency/vintaging, no persistence. All Massive specifics live only in
`engine/baculus/adapters/massive/`.

The adapter supports FIXTURE and LIVE modes. Secrets (`MASSIVE_API_KEY`) are read
from the environment at call time and never logged, echoed, or stored.

## Consequences

- Source B is a new adapter; everything downstream is unchanged.
- Fixture vs live provenance is explicit (`FetchMode`), so fixture proof is never
  mistaken for live evidence.
- The adapter is the single audited choke point for vendor secret handling.
