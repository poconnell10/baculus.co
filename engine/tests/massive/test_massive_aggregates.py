"""Offline tests for the Massive aggregates normalizer (no network).

Exercises ``massive_aggregates_to_bars`` against a retained envelope: the happy
path (ET business dates, exact Decimal prices) and every fail-closed
``MASSIVE_*`` reason. All vendor/identity/integrity defects must raise BEFORE any
canonical bar is produced.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from baculus.adapters.base import VendorArtifact
from baculus.adapters.massive.aggregates import (
    build_aggregates_envelope,
    massive_aggregates_to_bars,
)
from baculus.adapters.massive.reasons import MassiveError, MassiveReason
from baculus.lineage.canonical_json import to_canonical_bytes
from baculus.models.enums import FetchMode


def _artifact_from_page_text(page_text: str) -> VendorArtifact:
    """Wrap a single verbatim page body (which may carry vendor quirks like NaN)
    in a retained aggregates envelope + artifact."""
    env = build_aggregates_envelope(
        endpoint="/v2/aggs/ticker/AAPL/range/1/day/2020-01-01/2020-01-31",
        request={"ticker": "AAPL", "from": "2020-01-01", "to": "2020-01-31"},
        adjusted=True,
        pages_body_text=[page_text],
        request_ids=["r"],
        http_status=[200],
    )
    return VendorArtifact(
        source="massive",
        dataset="stocks/daily",
        payload=to_canonical_bytes(env),
        content_type="application/json",
        file_extension="json",
        fetch_mode=FetchMode.LIVE,
        retrieved_at=datetime(2020, 2, 1, tzinfo=ZoneInfo("UTC")),
        event_date_min=date(2020, 1, 1),
        event_date_max=date(2020, 1, 31),
        row_count=1,
    )


def test_happy_path_maps_et_business_dates_and_decimal_prices(mv):
    rows = [
        mv.row(date(2020, 1, 2), 100.11, 101.22, 99.55, 100.99, 12345),
        mv.row(date(2020, 1, 3), 101.00, 102.00, 100.50, 101.75, 6789),
    ]
    art = mv.envelope_artifact([mv.response("AAPL", rows)])
    bars = massive_aggregates_to_bars(art)

    assert [b.event_date for b in bars] == [date(2020, 1, 2), date(2020, 1, 3)]
    assert all(b.symbol == "AAPL" for b in bars)
    # Exact vendor text preserved as Decimal(18,8); no float drift.
    assert bars[0].open == Decimal("100.11000000")
    assert bars[0].close == Decimal("100.99000000")
    assert bars[0].volume == 12345
    assert bars[0].source == "massive"
    assert bars[0].source_artifact_id == art.artifact_id


def test_ticker_mismatch_fails_closed(mv):
    # Envelope requested AAPL but a page reports a different ticker.
    row = mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 10)
    art = mv.envelope_artifact([mv.response("MSFT", [row])])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_TICKER_MISMATCH


def test_invalid_timestamp_fails_closed(mv):
    bad = {"t": "2020-01-02", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}
    art = mv.envelope_artifact([mv.response("AAPL", [bad])])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_TIMESTAMP_INVALID


def test_out_of_requested_range_fails_closed(mv):
    row = mv.row(date(2020, 2, 15), 1, 2, 0.5, 1.5, 10)  # after the [01-01, 01-31] window
    art = mv.envelope_artifact([mv.response("AAPL", [row])])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_OUTSIDE_REQUEST_RANGE


def test_duplicate_business_date_fails_closed(mv):
    rows = [
        mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 10),
        mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 11),
    ]
    art = mv.envelope_artifact([mv.response("AAPL", rows)])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_BUSINESS_DATE_DUPLICATE


def test_ohlc_invariant_violation_fails_closed(mv):
    # low > high is impossible OHLC.
    bad = {"t": mv.et_ms(date(2020, 1, 2)), "o": 1, "h": 1, "l": 5, "c": 1, "v": 10}
    art = mv.envelope_artifact([mv.response("AAPL", [bad])])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_OHLC_INVALID


def test_negative_volume_fails_closed(mv):
    bad = {"t": mv.et_ms(date(2020, 1, 2)), "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": -3}
    art = mv.envelope_artifact([mv.response("AAPL", [bad])])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_VOLUME_INVALID


def test_non_finite_price_fails_closed(mv):
    # A raw page body carrying a JSON non-number (NaN) for a price.
    t = mv.et_ms(date(2020, 1, 2))
    page_text = (
        '{"ticker":"AAPL","status":"OK","request_id":"r",'
        f'"results":[{{"t":{t},"o":NaN,"h":2,"l":0.5,"c":1.5,"v":10}}]}}'
    )
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(_artifact_from_page_text(page_text))
    assert ei.value.reason is MassiveReason.MASSIVE_NUMERIC_NONFINITE


def test_vendor_error_status_fails_closed(mv):
    doc = mv.response("AAPL", [], status="ERROR")
    art = mv.envelope_artifact([doc])
    with pytest.raises(MassiveError) as ei:
        massive_aggregates_to_bars(art)
    assert ei.value.reason is MassiveReason.MASSIVE_UNEXPECTED_STATUS


def test_multi_page_envelope_concatenates_in_order(mv):
    p1 = mv.response("AAPL", [mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 10)])
    p2 = mv.response("AAPL", [mv.row(date(2020, 1, 3), 1, 2, 0.5, 1.5, 20)])
    art = mv.envelope_artifact([p1, p2])
    bars = massive_aggregates_to_bars(art)
    assert [b.event_date for b in bars] == [date(2020, 1, 2), date(2020, 1, 3)]


def test_null_results_page_is_not_malformed(mv):
    # A vendor page that explicitly reports no results (results: null) is empty,
    # not a parse failure.
    doc = {"ticker": "AAPL", "status": "OK", "request_id": "r", "results": None}
    art = mv.envelope_artifact([doc])
    assert massive_aggregates_to_bars(art) == []


def test_adjusted_flag_is_retained_in_envelope(mv):
    art = mv.envelope_artifact(
        [mv.response("AAPL", [mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 10)])],
        adjusted=False,
    )
    # Envelope preserves the adjustment basis so adjusted vs unadjusted are
    # distinct raw evidence, never silently merged.
    env = json.loads(art.payload.decode("utf-8"))
    assert env["adjusted"] is False
    assert env["request"]["adjusted"] is False
