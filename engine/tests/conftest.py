"""Shared pytest fixtures for the Baculus M0 test suite.

The control plane is Postgres — there is no in-memory mirror. Tests provision a
dedicated ``baculus_test`` database, apply the real migrations once, and isolate
each test by truncating the control-plane tables.

Point the suite at any Postgres via ``BACULUS_TEST_ADMIN_URL`` (a DSN whose
database is a maintenance DB the test role may CREATE/DROP from). Default:
``postgresql://baculus:baculus@localhost:5432/postgres``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.adapters.massive.fixtures.generator import OverrideMap
from baculus.governance.migrations import apply_migrations
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.reference.calendar import TradingCalendar
from baculus.storage.object_store import LocalObjectStore

FIXED_OBSERVATION = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)

_ADMIN_URL = os.environ.get(
    "BACULUS_TEST_ADMIN_URL", "postgresql://baculus:baculus@localhost:5432/postgres"
)
_TEST_DB = "baculus_test"


def _with_database(url: str, db_name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Create a fresh test database, apply migrations, return its DSN."""
    with psycopg.connect(_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{_TEST_DB}"')
    dsn = _with_database(_ADMIN_URL, _TEST_DB)
    apply_migrations(dsn)
    return dsn


def _truncate_all(conn: psycopg.Connection) -> None:
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'baculus'").fetchall()
    tables = [r["tablename"] for r in rows]
    if tables:
        qualified = ", ".join(f'baculus."{t}"' for t in tables)
        conn.execute(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session")
def calendar() -> TradingCalendar:
    return TradingCalendar("XNYS")


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_OBSERVATION


@pytest.fixture
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "obj")


@pytest.fixture
def store(pg_dsn):
    s = GovernanceStore(pg_dsn, clock=lambda: FIXED_OBSERVATION)
    _truncate_all(s.connection)
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


@pytest.fixture
def make_runner(store, object_store, calendar, fixed_clock):
    """Factory that builds a runner over a shared store/object_store."""

    def _factory(overrides: OverrideMap | None = None) -> IngestionRunner:
        adapter = MassiveAdapter(calendar=calendar, clock=fixed_clock, fixture_overrides=overrides)
        return IngestionRunner(
            store=store,
            object_store=object_store,
            adapter=adapter,
            calendar=calendar,
        )

    return _factory
