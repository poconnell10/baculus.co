"""Deterministic validation rules.

Covers required tests:
  5. malformed daily bar is rejected or quarantined
  6. duplicate symbol/date is detected
  7. impossible OHLC is detected
  8. negative volume is rejected
  9. missing expected trading date is detected
 10. weekends do not create false missing-bar findings
 11. known exchange holidays do not create false missing-bar findings
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from baculus.adapters.base import MarketDataSourceAdapter, SourceRequest, VendorArtifact
from baculus.adapters.massive import MassiveAdapter
from baculus.adapters.massive.normalize import MassiveParseError, massive_payload_to_bars
from baculus.ingestion.runner import IngestionRunner
from baculus.models.bar import CanonicalDailyBar
from baculus.models.enums import DatasetState, FetchMode
from baculus.normalization.canonical import build_canonical_frame
from baculus.validation.rules import RuleId
from baculus.validation.validator import Validator

_OBS = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)


def _bar(symbol: str, day: date, o=10.0, h=11.0, low=9.0, c=10.5, v=1000) -> CanonicalDailyBar:
    return CanonicalDailyBar(
        symbol=symbol,
        event_date=day,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        source="massive",
        observation_time=_OBS,
        source_artifact_id="artifact-abc",
    )


def _validate(bars, calendar, symbols, start, end, expected_sessions=None):
    df = build_canonical_frame(bars)
    return Validator(calendar=calendar).validate(
        df,
        source="massive",
        artifact_id="artifact-abc",
        symbols=symbols,
        start=start,
        end=end,
        expected_sessions=expected_sessions,
    )


# --- #5 malformed -----------------------------------------------------------
def test_malformed_payload_is_rejected_by_parser():
    artifact = VendorArtifact(
        source="massive",
        dataset="stocks/daily",
        payload=b'{"results":[{"T":"SPY","t":1704153600000,"o":"NOT_A_NUMBER","h":1,"l":1,"c":1,"v":1}]}',
        content_type="application/json",
        file_extension="json",
        fetch_mode=FetchMode.FIXTURE,
        retrieved_at=_OBS,
        event_date_min=date(2024, 1, 2),
        event_date_max=date(2024, 1, 2),
        row_count=1,
    )
    with pytest.raises(MassiveParseError):
        massive_payload_to_bars(artifact)


class _MalformedAdapter(MarketDataSourceAdapter):
    """Adapter that returns a structurally malformed Massive payload."""

    @property
    def source_id(self) -> str:
        return "massive"

    def fetch(self, request: SourceRequest) -> VendorArtifact:
        return VendorArtifact(
            source="massive",
            dataset=request.dataset,
            payload=b'{"results":[{"T":"SPY","t":1704153600000,"o":1,"h":1,"l":1,"c":1,"v":"BAD"}]}',
            content_type="application/json",
            file_extension="json",
            fetch_mode=FetchMode.FIXTURE,
            retrieved_at=_OBS,
            event_date_min=date(2024, 1, 2),
            event_date_max=date(2024, 1, 2),
            row_count=1,
        )

    def parse(self, artifact: VendorArtifact):
        return massive_payload_to_bars(artifact)

    def describe_request(self, request: SourceRequest) -> dict[str, str]:
        return {"source": "massive", "dataset": request.dataset}


def test_malformed_payload_is_quarantined_by_runner(store, object_store, calendar):
    runner = IngestionRunner(
        store=store,
        object_store=object_store,
        adapter=_MalformedAdapter(),
        calendar=calendar,
    )
    result = runner.ingest(
        SourceRequest(
            dataset="stocks/daily", symbols=("SPY",), start=date(2024, 1, 2), end=date(2024, 1, 2)
        )
    )
    assert result.quarantined is True
    assert result.state is DatasetState.QUARANTINED
    assert any(f.rule_id is RuleId.MALFORMED_RECORD for f in result.findings)


# --- #6 duplicate symbol/date ----------------------------------------------
def test_duplicate_symbol_date_detected(calendar):
    day = date(2024, 1, 2)
    bars = [_bar("SPY", day), _bar("SPY", day)]
    report = _validate(bars, calendar, ("SPY",), day, day, expected_sessions=[day])
    assert report.by_rule(RuleId.DUPLICATE_SYMBOL_DATE)
    assert report.passed is False


# --- #7 impossible OHLC -----------------------------------------------------
def test_impossible_ohlc_detected(calendar):
    day = date(2024, 1, 2)
    # high (5) below open (10): impossible.
    bars = [_bar("SPY", day, o=10.0, h=5.0, low=4.0, c=6.0)]
    report = _validate(bars, calendar, ("SPY",), day, day, expected_sessions=[day])
    assert report.by_rule(RuleId.IMPOSSIBLE_OHLC)
    assert report.passed is False


# --- #8 negative volume -----------------------------------------------------
def test_negative_volume_rejected(calendar):
    day = date(2024, 1, 2)
    bars = [_bar("SPY", day, v=-1)]
    report = _validate(bars, calendar, ("SPY",), day, day, expected_sessions=[day])
    assert report.by_rule(RuleId.NEGATIVE_VOLUME)
    assert report.passed is False


# --- #9 missing expected trading date ---------------------------------------
def test_missing_expected_trading_date_detected(calendar):
    # 2024-01-02, 03, 04 are all XNYS sessions. Provide only the 2nd and 4th.
    bars = [_bar("SPY", date(2024, 1, 2)), _bar("SPY", date(2024, 1, 4))]
    report = _validate(bars, calendar, ("SPY",), date(2024, 1, 2), date(2024, 1, 4))
    missing = report.by_rule(RuleId.MISSING_TRADING_DATE)
    assert [f.event_date for f in missing] == [date(2024, 1, 3)]


# --- #10 weekends do not create false missing-bar findings ------------------
def test_weekends_do_not_create_false_missing_findings(calendar):
    # 2024-01-05 is a Friday session; 06/07 are the weekend; 08 is Monday.
    bars = [_bar("SPY", date(2024, 1, 5)), _bar("SPY", date(2024, 1, 8))]
    report = _validate(bars, calendar, ("SPY",), date(2024, 1, 5), date(2024, 1, 8))
    missing_dates = {f.event_date for f in report.by_rule(RuleId.MISSING_TRADING_DATE)}
    assert date(2024, 1, 6) not in missing_dates  # Saturday
    assert date(2024, 1, 7) not in missing_dates  # Sunday
    assert missing_dates == set()  # nothing missing at all


# --- #11 holidays do not create false missing-bar findings ------------------
def test_holidays_do_not_create_false_missing_findings(calendar):
    # 2024-01-15 (MLK Day) is an XNYS holiday. Sessions around it: 12th (Fri),
    # 16th (Tue). Provide those two; the 15th must NOT be flagged missing.
    bars = [_bar("SPY", date(2024, 1, 12)), _bar("SPY", date(2024, 1, 16))]
    report = _validate(bars, calendar, ("SPY",), date(2024, 1, 12), date(2024, 1, 16))
    missing_dates = {f.event_date for f in report.by_rule(RuleId.MISSING_TRADING_DATE)}
    assert date(2024, 1, 15) not in missing_dates  # MLK Day holiday
    assert missing_dates == set()


# --- clean cohort: zero missing-bar findings across the full quarter --------
def test_full_cohort_has_no_missing_bar_findings(calendar, fixed_clock):
    adapter = MassiveAdapter(calendar=calendar, clock=fixed_clock)
    req = SourceRequest(
        dataset="stocks/daily",
        symbols=("SPY", "XLK", "XLE"),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
    )
    artifact = adapter.fetch(req)
    bars = adapter.parse(artifact)
    df = build_canonical_frame(bars)
    report = Validator(calendar=calendar).validate(
        df,
        source="massive",
        artifact_id=artifact.artifact_id,
        symbols=req.normalized_symbols(),
        start=req.start,
        end=req.end,
    )
    assert report.passed is True
    assert report.warning_count == 0  # no missing-trading-date warnings either


# --- #schema mismatch (bonus, required rule MD008) --------------------------
def test_schema_mismatch_detected(calendar):
    df = pl.DataFrame({"totally": ["wrong"], "columns": [1]})
    report = Validator(calendar=calendar).validate(
        df,
        source="massive",
        artifact_id="artifact-abc",
        symbols=("SPY",),
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
    )
    assert report.by_rule(RuleId.SCHEMA_MISMATCH)
    assert report.passed is False
