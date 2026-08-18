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
from decimal import Decimal
from typing import Any

import polars as pl

# --- Decimal price policy ---------------------------------------------------
# Prices are NEVER stored as binary floating point at the canonical/persisted
# boundary. Money is fixed-precision decimal. This policy is deliberately
# generous so that reconstructing decades of split/dividend adjustments does not
# accumulate rounding error. See docs/decisions/ADR-0008-decimal-price-policy.md.
PRICE_DECIMAL_PRECISION = 18  # total significant digits
PRICE_DECIMAL_SCALE = 8  # fractional digits (1e-8 minimum tick representable)
PRICE_DTYPE = pl.Decimal(precision=PRICE_DECIMAL_PRECISION, scale=PRICE_DECIMAL_SCALE)

# Intentional Polars schema for the canonical daily bar. Used with strict frame
# construction so that a type mismatch surfaces as an error rather than a silent
# cast. Order is stable for deterministic serialization. Values are Polars
# dtypes (classes and/or instances), which Polars accepts interchangeably.
CANONICAL_BAR_SCHEMA: dict[str, Any] = {
    "symbol": pl.Utf8,
    "event_date": pl.Date,
    "open": PRICE_DTYPE,
    "high": PRICE_DTYPE,
    "low": PRICE_DTYPE,
    "close": PRICE_DTYPE,
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

# Economic (market-data) identity fields: the canonical observations that define
# *what happened in the market*. Deliberately EXCLUDES observation_time and
# source_artifact_id (per-observation provenance) and, of course, any vendor
# request/transport metadata. Two artifacts with the same economic content over
# these fields are the same logical market data even if their raw bytes differ.
ECONOMIC_BAR_FIELDS: tuple[str, ...] = (
    "symbol",
    "event_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


@dataclass(frozen=True, slots=True)
class CanonicalDailyBar:
    """A single vendor-neutral daily OHLCV bar.

    Prices are ``Decimal`` (fixed precision) — never binary float — at the
    canonical boundary. Volume is an integer count of shares.
    """

    symbol: str
    event_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
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
    "ECONOMIC_BAR_FIELDS",
    "OHLC_FIELDS",
    "PRICE_DECIMAL_PRECISION",
    "PRICE_DECIMAL_SCALE",
    "PRICE_DTYPE",
    "REQUIRED_BAR_FIELDS",
    "CanonicalDailyBar",
]
