# ADR-0009: Massive vendor semantics (verification gate)

- Status: **PROPOSED — UNVERIFIED.** Blocked on live Massive access.
- Date: 2024 (M0 integration)

## Context

Before Baculus trusts the Massive adapter for live data, we must establish — from
the **current** Massive documentation and observed API behavior, not from
assumptions — exactly what Massive returns. The engine's canonical model and
validation depend on getting this right; guessing here would silently corrupt
provenance.

**This ADR must not be filled in from memory or inference.** It is completed by
running `scripts/verify_live.py` (which captures a real raw response to
`docs/evidence/massive_raw_sample.json`) and reading Massive's current docs, then
recording the evidence below.

> ⚠️ The Massive adapter's current field mapping and endpoint
> (`engine/baculus/adapters/massive/adapter.py`, `normalize.py`) are a
> **provisional assumption** modeled on a common aggregates shape
> (`T/t/o/h/l/c/v`). They are NOT yet reconciled against live Massive and must be
> corrected to match the findings below before any live evidence is trusted.

## Questions to answer (each needs cited evidence)

1. **Adjusted vs unadjusted.** Does the selected daily endpoint return adjusted
   or unadjusted OHLC? Is there an explicit parameter? What is the default?
   Baculus requires **unadjusted** raw bars (adjustments are reconstructed).
   - Evidence: _UNVERIFIED_
2. **Splits.** How are splits represented (separate endpoint? ratio convention?
   ex-date semantics?)?
   - Evidence: _UNVERIFIED_
3. **Dividends.** How are cash dividends represented (amount, currency, ex/record/
   pay dates)? Special/return-of-capital handling?
   - Evidence: _UNVERIFIED_
4. **Symbol / security identity.** What identifier is authoritative (ticker vs a
   stable id)? How are ticker changes, delistings, and re-uses represented?
   - Evidence: _UNVERIFIED_
5. **Timestamps / timezone.** What does the bar timestamp mean (session date vs
   epoch), in which timezone, and how does it map to an XNYS session date?
   - Evidence: _UNVERIFIED_
6. **Pagination / completeness.** How is a multi-symbol, multi-month range
   paginated? How do we know a response is complete (cursors, counts, limits)?
   - Evidence: _UNVERIFIED_
7. **Corrections / restatements.** Does Massive restate history? How is a
   correction signaled (a flag, a re-delivery, a separate feed)? This determines
   how our vintaging observes real restatements vs synthetic ones.
   - Evidence: _UNVERIFIED_
8. **Rate limits / auth.** Auth mechanism, rate limits, and error semantics that
   the adapter must handle.
   - Evidence: _UNVERIFIED_

## Decision

_Pending. To be recorded once the questions above are answered from live
evidence. At minimum: the exact endpoint, the unadjusted-bars guarantee, the
field-to-canonical mapping, the timestamp→session mapping, the pagination
completeness rule, and the corrections signal._

## Consequences

- Until this ADR is Accepted with evidence, live Massive output is treated as
  unverified: it may be ingested for engineering exercises but its correctness is
  not asserted, and it must not back any SEALED/M9 claim.
- Reconciling the adapter to the findings here is a prerequisite for M0
  Integration PASS.
