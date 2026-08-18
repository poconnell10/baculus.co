"""Logical market-data identity (distinct from raw artifact identity).

``raw_sha256`` (in the adapter/storage layer) is the SHA-256 of the exact vendor
bytes — immutable raw provenance and the content-address. It changes whenever any
byte changes, including volatile non-market metadata (request ids, generated
timestamps, trace/transport/pagination metadata).

``logical_data_sha256`` is the deterministic hash of the canonical market
*observations* only (``ECONOMIC_BAR_FIELDS``), excluding all of that volatile
metadata. It is what economic vintaging and restatement detection key on: a raw
byte change with identical logical data is a new raw observation, NOT a
restatement.
"""

from __future__ import annotations

from datetime import date

from baculus.lineage.canonical_json import digest_canonical
from baculus.models.bar import ECONOMIC_BAR_FIELDS, CanonicalDailyBar


def _economic_row(bar: CanonicalDailyBar) -> dict[str, object]:
    return {field: getattr(bar, field) for field in ECONOMIC_BAR_FIELDS}


def logical_observations(bars: list[CanonicalDailyBar]) -> list[dict[str, object]]:
    """Canonical, order-stable list of economic observations."""
    return sorted(
        (_economic_row(b) for b in bars),
        key=lambda r: (str(r["symbol"]), str(r["event_date"])),
    )


def logical_data_sha256(bars: list[CanonicalDailyBar]) -> str:
    """Deterministic hash of the economic observations only."""
    return digest_canonical(logical_observations(bars))


def observation_key(bar: CanonicalDailyBar) -> tuple[str, date]:
    return (bar.symbol, bar.event_date)


def changed_observations(
    prior: list[CanonicalDailyBar], current: list[CanonicalDailyBar]
) -> list[str]:
    """Identify exactly which (symbol, event_date) observations changed.

    Compares per-observation economic content between two bar sets and returns
    the affected keys (added, removed, or value-changed), sorted and stringified.
    """
    prior_map = {observation_key(b): _economic_row(b) for b in prior}
    current_map = {observation_key(b): _economic_row(b) for b in current}
    changed: set[tuple[str, date]] = set()
    for key in set(prior_map) | set(current_map):
        if prior_map.get(key) != current_map.get(key):
            changed.add(key)
    return [f"{sym}:{day.isoformat()}" for sym, day in sorted(changed)]


__all__ = [
    "changed_observations",
    "logical_data_sha256",
    "logical_observations",
    "observation_key",
]
