"""Market-data source adapters.

The adapter layer is the *only* place vendor-specific behavior is allowed to
live. Everything downstream consumes the vendor-neutral abstractions defined in
:mod:`baculus.adapters.base`.
"""

from __future__ import annotations

from baculus.adapters.base import (
    MarketDataSourceAdapter,
    SourceRequest,
    VendorArtifact,
)

__all__ = [
    "MarketDataSourceAdapter",
    "SourceRequest",
    "VendorArtifact",
]
