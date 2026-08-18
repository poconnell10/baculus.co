"""Trading calendar behavior and the Massive adapter boundary."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.models.enums import FetchMode


# --- calendar ---------------------------------------------------------------
def test_calendar_excludes_weekends_and_holidays(calendar):
    sessions = set(calendar.sessions_in_range(date(2024, 1, 1), date(2024, 1, 16)))
    assert date(2024, 1, 1) not in sessions  # New Year holiday
    assert date(2024, 1, 6) not in sessions  # Saturday
    assert date(2024, 1, 7) not in sessions  # Sunday
    assert date(2024, 1, 15) not in sessions  # MLK Day holiday
    assert date(2024, 1, 2) in sessions
    assert date(2024, 1, 16) in sessions


def test_calendar_reference_descriptor_is_pinnable(calendar):
    desc = calendar.reference_descriptor()
    assert desc["calendar_name"] == "XNYS"
    assert desc["calendar_provider"] == "exchange_calendars"
    assert desc["calendar_library_version"]
    assert desc["calendar_vintage"] == desc["calendar_library_version"]


def test_expected_availability_time_differs_from_session_date(calendar):
    session = date(2024, 1, 2)
    avail = calendar.expected_availability_time(session, timedelta(minutes=90))
    # Availability is a wall-clock instant strictly after the session date start.
    assert avail.tzinfo is not None
    assert avail > datetime(2024, 1, 2, tzinfo=UTC)


# --- adapter boundary -------------------------------------------------------
def test_adapter_fetch_is_byte_deterministic(calendar, fixed_clock):
    req = SourceRequest(
        dataset="stocks/daily", symbols=("SPY", "XLK"), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    a1 = MassiveAdapter(calendar=calendar, clock=fixed_clock).fetch(req)
    a2 = MassiveAdapter(calendar=calendar, clock=fixed_clock).fetch(req)
    assert a1.payload == a2.payload
    assert a1.sha256 == a2.sha256
    assert a1.fetch_mode is FetchMode.FIXTURE


def test_fixture_nonce_isolates_bytes_but_stays_deterministic(calendar, fixed_clock):
    req = SourceRequest(
        dataset="stocks/daily", symbols=("SPY",), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )

    def fetch(nonce):
        return MassiveAdapter(calendar=calendar, clock=fixed_clock, fixture_nonce=nonce).fetch(req)

    # A nonce isolates runs (distinct bytes/sha)...
    assert fetch("run-a").sha256 != fetch("run-b").sha256
    # ...but is deterministic for a fixed nonce (same run reproduces identically).
    assert fetch("run-a").sha256 == fetch("run-a").sha256
    # Default (no nonce) leaves bytes unchanged from the plain fixture.
    assert (
        fetch(None).sha256 == MassiveAdapter(calendar=calendar, clock=fixed_clock).fetch(req).sha256
    )


def test_adapter_parses_to_canonical_bars_with_both_time_axes(calendar, fixed_clock):
    req = SourceRequest(
        dataset="stocks/daily", symbols=("SPY",), start=date(2024, 1, 2), end=date(2024, 1, 3)
    )
    adapter = MassiveAdapter(calendar=calendar, clock=fixed_clock)
    artifact = adapter.fetch(req)
    bars = adapter.parse(artifact)
    assert {b.event_date for b in bars} == {date(2024, 1, 2), date(2024, 1, 3)}
    for b in bars:
        assert b.source == "massive"
        assert b.observation_time == artifact.retrieved_at  # observation axis
        assert b.source_artifact_id == artifact.artifact_id  # lineage to raw sha
        # Every canonical bar is internally consistent.
        assert b.high >= b.open >= b.low
        assert b.high >= b.close >= b.low
        assert b.volume >= 0


def test_describe_request_contains_no_secret(calendar, fixed_clock):
    req = SourceRequest(
        dataset="stocks/daily", symbols=("SPY",), start=date(2024, 1, 2), end=date(2024, 1, 3)
    )
    meta = MassiveAdapter(calendar=calendar, clock=fixed_clock).describe_request(req)
    joined = " ".join(f"{k}={v}" for k, v in meta.items()).lower()
    assert "key" not in joined
    assert "authorization" not in joined
    assert "bearer" not in joined
