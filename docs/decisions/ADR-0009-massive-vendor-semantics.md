# ADR-0009: Massive vendor semantics (contract + verification gate)

- Status: **PROPOSED — grounded in the vendor client, pending live re-confirmation.**
  The M0 contract below is derived from Massive's official Python client
  (`massive-com/client-python`, a fork of `polygon-api-client`) and is
  re-confirmed on the first live call by `scripts/verify_massive.py`. It is not
  asserted as verified until that proof runs against the real API with a key.
- Date: 2024 (M0 integration); contract section added for Prompt 004.

## Context

Before Baculus trusts the Massive adapter for live data, we must establish — from
Massive's **actual** client/documentation, not from assumptions — exactly what
Massive returns. The engine's canonical model and validation depend on getting
this right; guessing here would silently corrupt provenance.

The Massive REST surface is Polygon-compatible: `massive-com/client-python` is a
fork of `polygon-api-client`, so the endpoint shapes, response envelope, field
names, pagination, and auth mirror Polygon's aggregates + reference APIs. The M0
adapter implements those directly with the standard library (no SDK dependency);
the client is the *reference*, not a runtime dependency.

**This ADR is not filled in from memory.** The grounded contract is read from the
vendor client; the residual live-only facts (exact host, rate limits, whether the
vendor ever restates history) are captured by `scripts/verify_massive.py`, which
runs the full adapter path and writes evidence to
`docs/evidence/m0_massive_live_proof.json`.

## Grounded M0 contract (from `massive-com/client-python`)

Scope: **daily historical OHLCV for U.S. equities** — the only surface M0 uses.

- **Base URL.** `https://api.massive.com` (overridable via `MASSIVE_BASE_URL`).
  The exact host is re-confirmed on the first live call; the client's base URL is
  the source of truth.
- **Authentication.** `Authorization: Bearer <MASSIVE_API_KEY>` request header.
  The key is **never** placed in a URL or query string, never logged, never
  persisted. Enforced by `MassiveHTTPClient` and asserted by tests
  (`test_massive_http.py::test_secret_only_in_header_never_in_url`,
  `test_massive_live_path.py::test_live_secret_never_appears_in_persisted_provenance`).
- **Aggregates endpoint.**
  `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`
  with query `adjusted`, `sort`, `limit`. M0 uses `multiplier=1`,
  `timespan=day`, `sort=asc`, `limit=50000`, `from`/`to` as ISO dates.
- **Reference endpoint.** `GET /v3/reference/tickers/{ticker}` returns a
  `results` object (some deployments a single-element list) carrying `ticker`,
  `name`, `market`, `active`, … Used as the auth + identity probe.
- **Response envelope keys.** `ticker`, `adjusted`, `status` (`OK`/`DELAYED`),
  `request_id`, `resultsCount`/`queryCount`/`count`, `results`, optional
  `next_url`.
- **Per-result (bar) keys.** `t` (Unix **milliseconds**, the ET day boundary of
  the session), `o`/`h`/`l`/`c` (OHLC), `v` (volume), `vw` (VWAP), `n`
  (transaction count). Prices are parsed with `parse_float=Decimal` to preserve
  the vendor's exact textual value at `Decimal(18,8)` (ADR-0008); volume must be
  a non-negative integer.
- **Timestamp → session date.** `t` is converted to `America/New_York` and its
  **calendar date** is the canonical business date — never a UTC truncation
  (which would misdate the boundary bar). Reconciled against XNYS sessions.
- **Pagination / completeness.** A response may carry `next_url`; the client
  follows **only** that vendor-provided continuation (path + query, re-signed with
  the auth header) until it is absent. No page count or cursor is invented. A
  hard `max_pages` guard fails closed rather than looping unbounded.
- **request_id.** Returned per response (header `x-request-id` and/or body
  `request_id`); retained in the aggregates envelope as provenance. It is
  **volatile metadata**: it varies across otherwise-identical fetches, so it is
  **excluded** from logical-data identity (ADR-0010) — a changed `request_id`
  alone is a raw-only re-observation, not a restatement.
- **Limit.** `limit` default 5000, max 50000 (Polygon-compatible). M0 requests
  50000 so a one-month daily window is a single page.
- **Errors / retry.** Retryable: HTTP 413, 429, 499, 500, 502, 503, 504 (bounded
  exponential backoff; `429` honors `Retry-After`). Not retried: `401`/`403`
  (auth) and other deterministic 4xx. Explicit connect/read timeout (no unbounded
  default). Every failure surfaces a machine-readable `MASSIVE_*` reason code.

## Adjusted vs unadjusted (M0 decision)

Prompt 004 fetches **adjusted** daily bars (`adjusted=true`) for the AAPL proof.
The adapter treats the adjustment basis as **first-class provenance**, not an
assumption:

- The `adjusted` flag is recorded in the request parameters and retained verbatim
  in the aggregates envelope (`test_massive_aggregates.py::test_adjusted_flag_is_retained_in_envelope`).
- Adjusted and unadjusted responses are therefore **distinct raw evidence** and
  are never silently merged.

Reconstructing unadjusted bars from splits/dividends is a later-milestone concern;
M0 records *which* basis was fetched rather than asserting a basis. The residual
corporate-actions questions below stay open until a milestone needs them.

## Residual questions (still live-only / later milestones)

1. **Exact host + rate limits.** Confirmed on first live call by
   `scripts/verify_massive.py`. — _pending live_
2. **Splits.** Representation (endpoint, ratio, ex-date). — _open (not needed for M0 daily OHLCV)_
3. **Dividends.** Amount/currency/ex-record-pay, special/ROC handling. — _open (not needed for M0)_
4. **Symbol identity over time.** Ticker changes, delistings, re-use. — _open (M0 keys on ticker within a fixed window)_
5. **Corrections / restatements.** Whether Massive restates history and how it is
   signaled. Baculus observes restatements structurally (logical-identity change
   → new vintage, ADR-0010) regardless of an explicit vendor flag. — _open_

## Decision

For M0, the adapter implements the grounded contract above: Bearer auth, the
`/v2/aggs` daily endpoint and `/v3/reference/tickers` lookup, the `t/o/h/l/c/v`
field mapping with ET business-date derivation and `Decimal(18,8)` prices,
`next_url` pagination, full retry/timeout handling with `MASSIVE_*` reason codes,
verbatim page retention in a content-addressable envelope, and validation before
any canonical propagation. The adjustment basis is retained, not assumed.

## Consequences

- Until `scripts/verify_massive.py` runs green against the real API, live Massive
  output is treated as **grounded but not yet verified**: it may be ingested and
  is provenance-complete, but its correctness is asserted only after the live
  proof, and it must not back any SEALED/M9 claim (SEALED still requires
  independent M4 reconciliation, which a single Massive source cannot satisfy).
- The field mapping and endpoints live in
  `engine/baculus/adapters/massive/aggregates.py`,
  `engine/baculus/adapters/massive/http.py`, and `adapter.py`; the reason codes in
  `reasons.py`. Any live finding that contradicts the grounded contract is fixed
  there and recorded here.
