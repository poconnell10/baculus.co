"""Full ingestion-path tests for the LIVE Massive adapter (no network).

The adapter runs in ``LIVE`` mode over a scripted HTTP transport, so the exact
production path is exercised offline: real HTTP client -> retained aggregates
envelope -> normalizer -> deterministic validation -> dataset build. Covers the
happy path to PROVISIONAL, provenance, replay (ADR-0010 raw-only change),
restatement, machine-readable quarantine reason codes, and secret redaction.
"""

from __future__ import annotations

from datetime import date

import pytest

from baculus.adapters.massive.reasons import MassiveError, MassiveReason
from baculus.models.enums import DatasetState, GovernanceEventType


def _bar_row(mv, d: date, i: int, *, volume: int | None = None):
    o = 100 + i
    return mv.row(d, o, o + 2, o - 2, o + 1, volume if volume is not None else 1000 + i)


def _aapl_jan2020(mv, calendar, *, adjusted=True, request_id="req_live_1", overrides=None):
    """A well-formed AAPL daily response covering every XNYS session in Jan 2020."""
    sessions = calendar.sessions_in_range(date(2020, 1, 1), date(2020, 1, 31))
    overrides = overrides or {}
    rows = []
    for i, d in enumerate(sessions):
        rows.append(_bar_row(mv, d, i, volume=overrides.get(d)))
    return mv.response("AAPL", rows, adjusted=adjusted, request_id=request_id)


def test_live_happy_path_reaches_provisional(make_live_runner, aapl_request, calendar, mv, store):
    doc = _aapl_jan2020(mv, calendar)
    ctx = make_live_runner([mv.ok(doc)])
    result = ctx.runner.ingest(aapl_request)

    assert result.state is DatasetState.PROVISIONAL  # single-source ceiling
    assert result.quarantined is False
    assert result.row_count == 21  # every XNYS session in Jan 2020
    assert result.is_new_content is True
    assert result.vintage == 1
    assert result.manifest_digest is not None
    # A single Massive-only build can never be SEALED (single-source ceiling).
    assert result.dataset_build_id is not None
    assert ctx.runner.attempt_seal(result.dataset_build_id) is False
    blocked = store.governance_events(event_type=GovernanceEventType.SEAL_BLOCKED)
    assert len(blocked) == 1


def test_live_records_provenance_and_retains_envelope(
    make_live_runner, aapl_request, calendar, mv, object_store, store
):
    doc = _aapl_jan2020(mv, calendar)
    ctx = make_live_runner([mv.ok(doc)])
    result = ctx.runner.ingest(aapl_request)

    # Raw artifact retained + content-addressed; bytes round-trip from the store.
    raw = object_store.get(result.object_path)
    assert raw is not None and len(raw) > 0
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == result.sha256
    # The retained payload is the deterministic aggregates envelope (verbatim page).
    text = raw.decode("utf-8")
    assert '"envelope_kind"' in text
    assert '"vendor": "massive"' in text or '"vendor":"massive"' in text
    # RAW_ARTIFACT_STORED governance event was raised for the new economic content.
    assert GovernanceEventType.RAW_ARTIFACT_STORED.value in result.governance_event_types


def test_live_replay_same_data_new_request_id_is_raw_only(
    make_live_runner, aapl_request, calendar, mv, store
):
    """ADR-0010: identical market data with a new vendor request id is a raw-only
    change (new provenance), NOT a restatement or a new economic vintage."""
    v1 = make_live_runner([mv.ok(_aapl_jan2020(mv, calendar, request_id="req_A"))]).runner.ingest(
        aapl_request
    )
    v2 = make_live_runner([mv.ok(_aapl_jan2020(mv, calendar, request_id="req_B"))]).runner.ingest(
        aapl_request
    )

    assert v2.sha256 != v1.sha256  # raw bytes differ (request id)
    assert v2.logical_data_sha256 == v1.logical_data_sha256  # market data identical
    assert v2.is_raw_only_change is True
    assert v2.is_restatement is False
    assert v2.vintage == v1.vintage == 1
    assert GovernanceEventType.RESTATEMENT_DETECTED.value not in v2.governance_event_types
    assert len(store.audit_events(action="raw_metadata_changed_same_logical")) == 1


def test_live_changed_bar_is_restatement_new_vintage(
    make_live_runner, aapl_request, calendar, mv, store
):
    make_live_runner([mv.ok(_aapl_jan2020(mv, calendar, request_id="req_A"))]).runner.ingest(
        aapl_request
    )
    # Vendor redelivers a CHANGED volume for one session.
    changed = _aapl_jan2020(
        mv, calendar, request_id="req_B", overrides={date(2020, 1, 2): 9_999_999}
    )
    v2 = make_live_runner([mv.ok(changed)]).runner.ingest(aapl_request)

    assert v2.is_restatement is True
    assert v2.is_raw_only_change is False
    assert v2.vintage == 2
    events = store.governance_events(event_type=GovernanceEventType.RESTATEMENT_DETECTED)
    assert len(events) == 1
    assert events[0]["payload"]["changed_observations"] == ["AAPL:2020-01-02"]


def test_live_ticker_mismatch_quarantines_with_reason_code(
    make_live_runner, aapl_request, calendar, mv, store
):
    # Requested AAPL, vendor page reports a different ticker: fail closed.
    doc = _aapl_jan2020(mv, calendar)
    doc["ticker"] = "MSFT"
    ctx = make_live_runner([mv.ok(doc)])
    result = ctx.runner.ingest(aapl_request)

    assert result.quarantined is True
    assert result.state is DatasetState.QUARANTINED
    assert result.validation_summary["quarantine_reason"] == (
        MassiveReason.MASSIVE_TICKER_MISMATCH.value
    )
    # The machine-readable reason code is recorded as the quarantine case reason.
    reasons = [
        r["reason"]
        for r in store.connection.execute("SELECT reason FROM quarantine_cases").fetchall()
    ]
    assert MassiveReason.MASSIVE_TICKER_MISMATCH.value in reasons


def test_live_ohlc_violation_quarantines_with_reason_code(
    make_live_runner, aapl_request, calendar, mv, store
):
    doc = _aapl_jan2020(mv, calendar)
    doc["results"][0]["l"] = 10_000  # low above high: impossible OHLC
    ctx = make_live_runner([mv.ok(doc)])
    result = ctx.runner.ingest(aapl_request)

    assert result.quarantined is True
    assert (
        result.validation_summary["quarantine_reason"] == MassiveReason.MASSIVE_OHLC_INVALID.value
    )


def test_live_http_error_is_reason_coded(make_live_runner, aapl_request, calendar, mv):
    # Server errors exhaust the retry budget and surface a reason-coded failure.
    ctx = make_live_runner([(500, {}, b"{}")] * 6)
    with pytest.raises(MassiveError) as ei:
        ctx.runner.ingest(aapl_request)
    assert ei.value.reason is MassiveReason.MASSIVE_HTTP_ERROR


def test_reference_lookup_returns_record(make_live_runner, calendar, mv):
    ref = {"ticker": "AAPL", "name": "Apple Inc.", "market": "stocks", "active": True}
    ctx = make_live_runner([mv.ok({"status": "OK", "request_id": "r", "results": ref})])
    got = ctx.adapter.reference_lookup("aapl")
    assert got["ticker"] == "AAPL"
    assert got["name"] == "Apple Inc."


def test_reference_lookup_ticker_mismatch_fails_closed(make_live_runner, mv):
    ref = {"ticker": "MSFT", "name": "Microsoft"}
    ctx = make_live_runner([mv.ok({"status": "OK", "request_id": "r", "results": ref})])
    with pytest.raises(MassiveError) as ei:
        ctx.adapter.reference_lookup("AAPL")
    assert ei.value.reason is MassiveReason.MASSIVE_TICKER_MISMATCH


def test_live_secret_never_appears_in_persisted_provenance(
    make_live_runner, aapl_request, calendar, mv, massive_key, object_store
):
    doc = _aapl_jan2020(mv, calendar)
    ctx = make_live_runner([mv.ok(doc)])
    result = ctx.runner.ingest(aapl_request)

    raw = object_store.get(result.object_path).decode("utf-8")
    assert massive_key not in raw
    # And the secret only ever traveled in the Authorization header.
    for url, headers, _timeout in ctx.transport.calls:
        assert massive_key not in url
        assert headers.get("Authorization") == f"Bearer {massive_key}"
