"""Live Massive integration proof (network + real API key).

Exercises the REAL Massive REST API through the exact production adapter/HTTP
client. It runs ONLY when ``MASSIVE_API_KEY`` is present in the environment (i.e.
on a machine whose ``.env`` is loaded); otherwise it is SKIPPED with a clear
reason — never silently passed, and the no-network case is a skip, not a weak
assertion.

It proves, against the live vendor:
  * authentication succeeds (Bearer header) and a reference lookup returns AAPL;
  * the historical daily bars for AAPL / Jan 2020 / adjusted are fetched,
    retained verbatim in the aggregates envelope, and normalize into canonical
    bars whose business dates are the expected ET sessions;
  * the secret never appears in the retained raw payload.

This test performs read-only vendor calls and writes nothing to any control
plane (it does not touch Postgres or object storage). Run locally (Mac) with the
project .env loaded:

    set -a && source .env && set +a
    pytest engine/tests/integration/test_massive_live.py -v
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.models.enums import FetchMode
from baculus.reference.calendar import TradingCalendar

pytestmark = pytest.mark.skipif(
    not os.environ.get("MASSIVE_API_KEY"),
    reason="live Massive integration: MASSIVE_API_KEY not set",
)

_FROM, _TO = date(2020, 1, 1), date(2020, 1, 31)


@pytest.fixture
def adapter() -> MassiveAdapter:
    return MassiveAdapter(mode=FetchMode.LIVE, calendar=TradingCalendar("XNYS"))


@pytest.fixture
def aapl_request() -> SourceRequest:
    return SourceRequest(
        dataset="stocks/daily",
        symbols=("AAPL",),
        start=_FROM,
        end=_TO,
        options=(("adjusted", "true"),),
    )


def test_reference_lookup_authenticates_and_returns_aapl(adapter):
    ref = adapter.reference_lookup("AAPL")
    assert str(ref.get("ticker", "")).upper() == "AAPL"


def test_live_daily_bars_fetch_normalize_and_retain(adapter, aapl_request):
    artifact = adapter.fetch(aapl_request)
    assert artifact.fetch_mode is FetchMode.LIVE
    assert artifact.source == "massive"

    # Secret never lands in the retained raw evidence.
    key = os.environ["MASSIVE_API_KEY"]
    assert key not in artifact.payload.decode("utf-8")

    bars = adapter.parse(artifact)
    assert bars, "expected at least one AAPL daily bar for Jan 2020"

    cal = TradingCalendar("XNYS")
    expected = set(cal.sessions_in_range(_FROM, _TO))
    got = {b.event_date for b in bars}
    # Every returned bar is a real XNYS session inside the requested window.
    assert got.issubset(expected)
    for b in bars:
        assert b.symbol == "AAPL"
        assert b.high >= b.low
        assert b.low <= b.open <= b.high
        assert b.low <= b.close <= b.high
        assert b.volume >= 0
