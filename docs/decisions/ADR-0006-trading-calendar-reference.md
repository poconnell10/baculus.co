# ADR-0006: Authoritative, versioned trading calendar

- Status: Accepted
- Date: 2024 (M0)

## Context

Detecting missing bars requires knowing which days *should* have data. Hand-rolled
"weekday minus holidays" logic is silently wrong (half-days, ad-hoc closures,
changing holiday rules) and unversioned.

## Decision

Use a pinned `exchange_calendars` `XNYS` calendar as authoritative reference
data. Record the provider, library version, and vintage in
`reference_data_versions` and in every manifest. Separate `expected_bar_date`
(the session, event time) from `expected_availability_time`
(`session_close + source SLA`, a wall-clock instant used for staleness).

## Consequences

- Missing-trading-date validation is correct by construction; weekends and
  holidays never produce false findings.
- The calendar version is auditable and pinnable per dataset; upgrades are
  intentional (they change reference data).
- Staleness reasoning has a source-specific availability model, not a single
  global assumption.
