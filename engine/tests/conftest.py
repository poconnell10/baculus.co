"""Shared pytest fixtures for the Baculus M0 test suite."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.adapters.massive.fixtures.generator import OverrideMap
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.reference.calendar import TradingCalendar
from baculus.storage.object_store import LocalObjectStore

FIXED_OBSERVATION = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def calendar() -> TradingCalendar:
    # Building the calendar is mildly expensive; share it across the session.
    return TradingCalendar("XNYS")


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_OBSERVATION


@pytest.fixture
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "obj")


@pytest.fixture
def store() -> GovernanceStore:
    s = GovernanceStore(":memory:", clock=lambda: FIXED_OBSERVATION)
    yield s
    s.close()


@pytest.fixture
def proof_request() -> SourceRequest:
    return SourceRequest(
        dataset="stocks/daily",
        symbols=("SPY", "XLK", "XLE"),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
    )


def make_adapter(
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
    overrides: OverrideMap | None = None,
) -> MassiveAdapter:
    return MassiveAdapter(calendar=calendar, clock=clock, fixture_overrides=overrides)


@pytest.fixture
def make_runner(store, object_store, calendar, fixed_clock):
    """Factory that builds a runner over a shared store/object_store.

    Accepts optional fixture overrides so a test can simulate a vendor
    restatement while reusing the same control plane and object store.
    """

    def _factory(overrides: OverrideMap | None = None) -> IngestionRunner:
        adapter = make_adapter(calendar, fixed_clock, overrides)
        return IngestionRunner(
            store=store,
            object_store=object_store,
            adapter=adapter,
            calendar=calendar,
        )

    return _factory
