"""Object storage for raw vendor artifacts and canonical datasets.

Postgres is the control/governance plane; large artifact bodies live here. For
the POC the production target is Supabase Storage; a local filesystem
implementation mirrors its semantics for dev and tests.
"""

from __future__ import annotations

from baculus.storage.object_store import LocalObjectStore, ObjectStore
from baculus.storage.raw_store import RawArtifactStore, RawStorePutResult

__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "RawArtifactStore",
    "RawStorePutResult",
]
