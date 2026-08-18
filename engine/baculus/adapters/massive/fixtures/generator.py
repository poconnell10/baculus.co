"""Deterministic generator for Massive-format daily-bar fixtures.

The output mimics a Massive aggregates response (ticker ``T``, epoch-ms ``t``,
``o/h/l/c/v`` fields). Values are a pure, deterministic function of
``(symbol, session_date)`` via SHA-256, so:

  * the same request always yields identical bytes (idempotency proof), and
  * an ``overrides`` map can force a *changed* historical value to simulate a
    vendor restatement (vintaging proof).

This module contains no I/O and no randomness.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

# The M0 proof cohort. Small on purpose: prove infrastructure, not performance.
PROOF_COHORT_SYMBOLS: tuple[str, ...] = ("SPY", "XLK", "XLE")
PROOF_COHORT_START: date = date(2024, 1, 1)
PROOF_COHORT_END: date = date(2024, 3, 31)

# Rough anchor price per symbol (arbitrary; values only need to be plausible and
# internally consistent — this is not investment data).
_BASE_PRICE: dict[str, float] = {"SPY": 470.0, "XLK": 200.0, "XLE": 85.0}
_DEFAULT_BASE = 100.0

# Type alias: overrides[(symbol, iso_date)] = {"o"|"h"|"l"|"c": float, "v": int}
OverrideMap = dict[tuple[str, str], dict[str, float]]


def _epoch_ms(session: date) -> int:
    dt = datetime(session.year, session.month, session.day, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _seed(symbol: str, session: date) -> int:
    digest = hashlib.sha256(f"{symbol}|{session.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _base_bar(symbol: str, session: date) -> dict[str, float | int | str]:
    """Compute the deterministic base OHLCV for one symbol/session."""
    seed = _seed(symbol, session)
    base = _BASE_PRICE.get(symbol, _DEFAULT_BASE)

    open_px = round(base + (seed % 10_000) / 100.0, 2)
    delta = ((seed >> 16) % 400 - 200) / 100.0
    close_px = round(open_px + delta, 2)
    spread = ((seed >> 32) % 150) / 100.0
    high_px = round(max(open_px, close_px) + spread + 0.01, 2)
    low_px = round(min(open_px, close_px) - spread - 0.01, 2)
    volume = 1_000_000 + (seed % 5_000_000)

    return {
        "T": symbol,
        "t": _epoch_ms(session),
        "o": open_px,
        "h": high_px,
        "l": low_px,
        "c": close_px,
        "v": volume,
        "n": 10_000 + (seed % 90_000),  # trade count, vendor-style extra field
    }


def build_massive_payload(
    symbols: tuple[str, ...],
    sessions: list[date],
    *,
    overrides: OverrideMap | None = None,
    nonce: str | None = None,
) -> dict[str, object]:
    """Build a deterministic Massive-format payload dict.

    ``sessions`` is the list of trading sessions to emit (the caller derives
    these from the authoritative calendar). ``overrides`` may replace individual
    OHLCV fields to simulate a vendor restatement. ``nonce`` (if given) is emitted
    as ``request_id`` — a benign per-request field real vendors return — so that
    otherwise-identical payloads can be isolated across proof runs. It never
    alters bar data. Output is byte-deterministic for a fixed set of inputs.
    """
    overrides = overrides or {}
    results: list[dict[str, float | int | str]] = []
    for symbol in sorted(symbols):
        for session in sorted(sessions):
            bar = _base_bar(symbol, session)
            key = (symbol, session.isoformat())
            if key in overrides:
                bar = {**bar, **overrides[key]}
            results.append(bar)

    # Sort deterministically for stable byte output.
    results.sort(key=lambda r: (str(r["T"]), int(r["t"])))
    doc: dict[str, object] = {
        "status": "OK",
        "adjusted": False,
        "resultsCount": len(results),
        "results": results,
    }
    if nonce is not None:
        doc["request_id"] = nonce
    return doc


__all__ = [
    "PROOF_COHORT_END",
    "PROOF_COHORT_START",
    "PROOF_COHORT_SYMBOLS",
    "OverrideMap",
    "build_massive_payload",
]
