"""Strict construction of the canonical Polars frame.

Frame construction uses the intentional canonical schema. Polars enforces the
declared types; a value that cannot be represented raises rather than being
silently coerced — that is the point.
"""

from __future__ import annotations

import polars as pl

from baculus.models.bar import CANONICAL_BAR_SCHEMA, CanonicalDailyBar


def build_canonical_frame(bars: list[CanonicalDailyBar]) -> pl.DataFrame:
    """Build a strictly-typed canonical frame from canonical bars.

    The column order matches ``CANONICAL_BAR_SCHEMA`` for deterministic output.
    """
    rows = [b.as_row() for b in bars]
    return pl.DataFrame(rows, schema=CANONICAL_BAR_SCHEMA, orient="row")


def frame_schema_matches(df: pl.DataFrame) -> bool:
    """Whether ``df``'s schema exactly matches the canonical bar schema."""
    actual = dict(df.schema)
    if list(actual.keys()) != list(CANONICAL_BAR_SCHEMA.keys()):
        return False
    return all(actual[name] == dtype for name, dtype in CANONICAL_BAR_SCHEMA.items())


__all__ = ["build_canonical_frame", "frame_schema_matches"]
