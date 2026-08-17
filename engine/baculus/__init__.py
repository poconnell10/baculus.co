"""Baculus engine — M0 vendor & infrastructure foundation.

Public API surface is intentionally small at M0. Downstream code should depend
on the vendor-neutral abstractions (canonical bars, the source-adapter
interface, the governance control plane) and never on vendor specifics.
"""

from __future__ import annotations

__version__ = "0.0.0"

# Schema version for the canonical daily-bar model. Bumped whenever the
# canonical representation changes in a way that affects stored artifacts.
CANONICAL_SCHEMA_VERSION = "1.0.0"

__all__ = ["CANONICAL_SCHEMA_VERSION", "__version__"]
