"""Object storage for raw vendor artifacts and canonical datasets.

Postgres is the control/governance plane; large artifact bodies live here. The
hosted-POC target is Supabase Storage (``SupabaseObjectStore``); a local
filesystem implementation (``LocalObjectStore``) mirrors its semantics for dev
and tests. Both satisfy the same ``ObjectStore`` contract.
"""

from __future__ import annotations

import os

from baculus.storage.object_store import LocalObjectStore, ObjectStore
from baculus.storage.raw_store import RawArtifactStore, RawStorePutResult
from baculus.storage.supabase_store import SupabaseObjectStore, SupabaseStorageError


def object_store_from_env() -> ObjectStore:
    """Select the object store from the environment.

    ``BACULUS_OBJECT_STORE=supabase`` selects Supabase Storage (requires
    SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY); anything else uses the local
    filesystem store rooted at ``BACULUS_DATA_ROOT`` (default ``./_data``).
    """
    backend = os.environ.get("BACULUS_OBJECT_STORE", "local").lower()
    if backend == "supabase":
        bucket = os.environ.get("BACULUS_STORAGE_BUCKET", "baculus-raw")
        return SupabaseObjectStore(bucket=bucket)
    root = os.environ.get("BACULUS_DATA_ROOT", "./_data")
    return LocalObjectStore(root)


__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "RawArtifactStore",
    "RawStorePutResult",
    "SupabaseObjectStore",
    "SupabaseStorageError",
    "object_store_from_env",
]
