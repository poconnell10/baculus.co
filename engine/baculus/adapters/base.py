"""The vendor-neutral market-data source boundary.

``MarketDataSourceAdapter`` is the contract every vendor must satisfy. Its
responsibilities are deliberately narrow:

  * identify the source;
  * fetch a requested dataset/range;
  * return the *original vendor artifact* verbatim (bytes are never mutated);
  * expose source/request metadata;
  * normalize its own vendor format into canonical, vendor-neutral bars.

Explicit non-responsibilities (these belong elsewhere and must not leak in):
  * no strategy logic;
  * no decision about which source is authoritative (that is governance/M4);
  * no idempotency/vintaging decisions (that is the ingestion runner);
  * no persistence (that is storage / the control plane).

A future "Source B" must be implementable by writing another adapter, with
**zero** changes to downstream validation or dataset-building logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

from baculus.lineage.canonical_json import sha256_hex
from baculus.models.bar import CanonicalDailyBar
from baculus.models.enums import FetchMode


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """A vendor-neutral request for a dataset over an inclusive date range.

    ``dataset`` is a logical, vendor-neutral dataset id (e.g. ``stocks/daily``).
    The adapter maps it onto whatever the vendor actually calls it.
    """

    dataset: str
    symbols: tuple[str, ...]
    start: date
    end: date
    # Free-form, vendor-neutral request options (e.g. adjustment=none). Kept
    # sorted/simple so it serializes deterministically into run metadata.
    options: tuple[tuple[str, str], ...] = ()

    def normalized_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({s.strip().upper() for s in self.symbols}))


@dataclass(frozen=True, slots=True)
class VendorArtifact:
    """The original vendor payload plus provenance metadata.

    ``payload`` holds the vendor bytes *verbatim*. It is what gets
    content-addressed (SHA-256) and stored in the raw object store. The adapter
    must never normalize in place — ``payload`` is the source of truth.
    """

    source: str
    dataset: str
    payload: bytes
    content_type: str
    file_extension: str
    fetch_mode: FetchMode
    # Observation time: when Baculus retrieved THIS version from the vendor.
    # This is one of the system's two fundamental time axes.
    retrieved_at: datetime
    # Two-time-axis + range metadata describing what this payload contains.
    event_date_min: date
    event_date_max: date
    row_count: int | None
    # Echo of the request parameters the vendor was actually asked for.
    request_parameters: dict[str, str] = field(default_factory=dict)
    # Opaque vendor-reported metadata (status, cursors, api version, ...).
    vendor_metadata: dict[str, str] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        """Content hash of the verbatim vendor payload."""
        return sha256_hex(self.payload)

    @property
    def artifact_id(self) -> str:
        """Stable id for this raw artifact = its content hash.

        Because storage is content-addressed, the artifact id and the SHA-256
        are the same value; canonical bars reference this to trace lineage.
        """
        return self.sha256

    @property
    def byte_size(self) -> int:
        return len(self.payload)


class MarketDataSourceAdapter(ABC):
    """Contract for a market-data vendor adapter."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier for this source (e.g. ``"massive"``)."""

    @abstractmethod
    def fetch(self, request: SourceRequest) -> VendorArtifact:
        """Fetch the requested dataset/range and return the raw vendor artifact.

        Implementations MUST return the vendor bytes unmodified in
        ``VendorArtifact.payload``.
        """

    @abstractmethod
    def parse(self, artifact: VendorArtifact) -> list[CanonicalDailyBar]:
        """Normalize a vendor artifact into canonical, vendor-neutral bars.

        This is the *only* place vendor field names/encodings are interpreted.
        Implementations must not silently coerce invalid types; malformed rows
        should be surfaced (raised or flagged) rather than guessed at.
        """

    @abstractmethod
    def describe_request(self, request: SourceRequest) -> dict[str, str]:
        """Return vendor-neutral, log-safe request metadata.

        MUST NOT include secrets (API keys, auth headers, signed URLs).
        """


__all__ = [
    "MarketDataSourceAdapter",
    "SourceRequest",
    "VendorArtifact",
]
