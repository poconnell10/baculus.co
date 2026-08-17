"""Canonical, vendor-neutral daily bar model.

Every market-data vendor must be normalized into this single representation
*inside its adapter*. Nothing downstream of an adapter is allowed to know about
vendor-specific field names or encodings.

Design rules:
  * Types are explicit. We never silently coerce; invalid input is rejected by
    the validation layer, not papered over here.
  * Two time axes are first-class: ``event_date`` (when the bar happened) and
    ``observation_time`` (when Baculus received this version from the vendor).
  * ``source_artifact_id`` ties a bar back to the exact raw vendor artifact
    (and therefore its SHA-256 and vintage) it was derived from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import polars as pl

# Intentional Polars schema for the canonical daily bar. Used with strict frame
# construction so that a type mismatch surfaces as an error rather than a silent
# cast. Order is stable for deterministic serialization. Values are Polars
# dtypes (classes and/or instances), which Polars accepts interchangeably.
CANONICAL_BAR_SCHEMA: dict[str, Any] = {
    "symbol": pl.Utf8,
    "event_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "source": pl.Utf8,
    "observation_time": pl.Datetime(time_unit="us", time_zone="UTC"),
    "source_artifact_id": pl.Utf8,
}

# Columns that must be non-null for a bar to be considered structurally present.
REQUIRED_BAR_FIELDS: tuple[str, ...] = (
    "symbol",
    "event_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
    "observation_time",
    "source_artifact_id",
)

# Price/volume fields for OHLC integrity checks.
OHLC_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")


@dataclass(frozen=True, slots=True)
class CanonicalDailyBar:
    """A single vendor-neutral daily OHLCV bar.

    Prices are decimals-as-float for the POC; a future milestone may move to a
    fixed-point/decimal representation. Volume is an integer count of shares.
    """

    symbol: str
    event_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    observation_time: datetime
    source_artifact_id: str

    def as_row(self) -> dict[str, object]:
        """Return a dict suitable for strict Polars frame construction."""
        return {
            "symbol": self.symbol,
            "event_date": self.event_date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source": self.source,
            "observation_time": self.observation_time,
            "source_artifact_id": self.source_artifact_id,
        }


__all__ = [
    "CANONICAL_BAR_SCHEMA",
    "OHLC_FIELDS",
    "REQUIRED_BAR_FIELDS",
    "CanonicalDailyBar",
]
