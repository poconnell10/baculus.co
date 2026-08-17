"""Content-addressed raw-artifact storage.

Raw vendor payloads are retained *verbatim* and addressed by the SHA-256 of
their bytes. Because the object path embeds the content hash:

  * an identical refetch resolves to the same path and is never rewritten;
  * changed content lands at a new path — history is never overwritten.

Logical path pattern:

    raw/<source>/<dataset>/observation_date=YYYY-MM-DD/<sha256>.<ext>

e.g. raw/massive/stocks/daily/observation_date=2024-04-01/<sha256>.json
"""

from __future__ import annotations

from dataclasses import dataclass

from baculus.adapters.base import VendorArtifact
from baculus.storage.object_store import ObjectStore


@dataclass(frozen=True, slots=True)
class RawStorePutResult:
    path: str
    sha256: str
    byte_size: int
    # False when the exact bytes were already stored (idempotent refetch).
    newly_written: bool


class RawArtifactStore:
    def __init__(self, object_store: ObjectStore, root_prefix: str = "raw") -> None:
        self._store = object_store
        self._root = root_prefix.strip("/")

    def build_path(self, artifact: VendorArtifact) -> str:
        observation_date = artifact.retrieved_at.date().isoformat()
        dataset = artifact.dataset.strip("/")
        return (
            f"{self._root}/{artifact.source}/{dataset}/"
            f"observation_date={observation_date}/{artifact.sha256}.{artifact.file_extension}"
        )

    def put(self, artifact: VendorArtifact) -> RawStorePutResult:
        path = self.build_path(artifact)
        newly_written = self._store.put(path, artifact.payload)
        return RawStorePutResult(
            path=path,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            newly_written=newly_written,
        )

    def get(self, path: str) -> bytes:
        return self._store.get(path)


__all__ = ["RawArtifactStore", "RawStorePutResult"]
