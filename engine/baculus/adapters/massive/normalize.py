"""Massive-specific normalization: raw Massive payload -> canonical bars.

This is the single point where Massive's field names and encodings are
interpreted. It is intentionally strict: an invalid type is never silently
coerced. Malformed rows are collected and reported via ``MassiveParseError`` so
the ingestion runner can quarantine them rather than admitting bad data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from baculus.adapters.base import VendorArtifact
from baculus.models.bar import CanonicalDailyBar


@dataclass(frozen=True, slots=True)
class RowError:
    index: int
    reason: str


class MassiveParseError(Exception):
    """Raised when a Massive payload contains malformed/unparseable rows."""

    def __init__(self, errors: list[RowError]):
        self.errors = errors
        detail = "; ".join(f"row {e.index}: {e.reason}" for e in errors[:5])
        more = "" if len(errors) <= 5 else f" (+{len(errors) - 5} more)"
        super().__init__(f"malformed Massive payload: {detail}{more}")


def _require_number(value: Any) -> float | None:
    # bool is a subclass of int — reject it explicitly.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _require_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def massive_payload_to_bars(artifact: VendorArtifact) -> list[CanonicalDailyBar]:
    """Parse a Massive daily-aggregates artifact into canonical bars.

    Raises ``MassiveParseError`` listing every row that could not be parsed.
    """
    try:
        doc = json.loads(artifact.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MassiveParseError(
            [RowError(index=-1, reason=f"payload not valid JSON: {exc}")]
        ) from exc

    results = doc.get("results")
    if not isinstance(results, list):
        raise MassiveParseError([RowError(index=-1, reason="missing 'results' array")])

    bars: list[CanonicalDailyBar] = []
    errors: list[RowError] = []
    for i, row in enumerate(results):
        if not isinstance(row, dict):
            errors.append(RowError(i, "row is not an object"))
            continue

        symbol = row.get("T")
        ts = row.get("t")
        o = _require_number(row.get("o"))
        h = _require_number(row.get("h"))
        low = _require_number(row.get("l"))
        c = _require_number(row.get("c"))
        v = _require_int(row.get("v"))

        problems: list[str] = []
        if not isinstance(symbol, str) or not symbol.strip():
            problems.append("invalid/missing ticker 'T'")
        ts_int = _require_int(ts)
        if ts_int is None:
            problems.append("invalid/missing timestamp 't'")
        for name, val in (("o", o), ("h", h), ("l", low), ("c", c)):
            if val is None:
                problems.append(f"invalid/missing price '{name}'")
        if v is None:
            problems.append("invalid/missing volume 'v'")

        if problems:
            errors.append(RowError(i, ", ".join(problems)))
            continue

        assert isinstance(symbol, str) and ts_int is not None  # narrowed above
        event_date = datetime.fromtimestamp(ts_int / 1000, tz=UTC).date()
        bars.append(
            CanonicalDailyBar(
                symbol=symbol.strip().upper(),
                event_date=event_date,
                open=o,  # type: ignore[arg-type]  # narrowed: not None
                high=h,  # type: ignore[arg-type]
                low=low,  # type: ignore[arg-type]
                close=c,  # type: ignore[arg-type]
                volume=v,  # type: ignore[arg-type]
                source=artifact.source,
                observation_time=artifact.retrieved_at,
                source_artifact_id=artifact.artifact_id,
            )
        )

    if errors:
        raise MassiveParseError(errors)
    return bars


__all__ = ["MassiveParseError", "RowError", "massive_payload_to_bars"]
