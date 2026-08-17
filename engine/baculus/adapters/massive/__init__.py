"""Massive vendor adapter.

All Massive-specific behavior (endpoints, auth, field names, payload shape) is
contained in this package and nowhere else in the codebase.
"""

from __future__ import annotations

from baculus.adapters.massive.adapter import MassiveAdapter

__all__ = ["MassiveAdapter"]
