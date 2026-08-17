"""Deterministic Massive fixtures for offline, reproducible M0 proof.

Fixture output is a byte-for-byte deterministic function of its inputs. This is
what lets us prove idempotency and vintaging without any live vendor access.
Fixture-derived evidence is always tagged ``FetchMode.FIXTURE`` so it can never
be mistaken for live-vendor proof.
"""

from __future__ import annotations

from baculus.adapters.massive.fixtures.generator import (
    PROOF_COHORT_END,
    PROOF_COHORT_START,
    PROOF_COHORT_SYMBOLS,
    build_massive_payload,
)

__all__ = [
    "PROOF_COHORT_END",
    "PROOF_COHORT_START",
    "PROOF_COHORT_SYMBOLS",
    "build_massive_payload",
]
