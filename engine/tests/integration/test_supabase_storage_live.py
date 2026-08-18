"""Hosted Supabase Storage integration proof (live).

This exercises the REAL Supabase Storage bucket. It runs only when the Supabase
credentials are present in the environment (i.e. on a machine whose `.env` is
loaded); otherwise it is SKIPPED with a clear reason — never silently passed.

It proves, against the real private bucket:
  * a raw payload uploads and reads back byte-identically;
  * identical bytes do not create a duplicate (content-addressed, create-only);
  * different bytes land at a different path and both remain retrievable
    (storage-layer vintaging);
  * no secret is ever placed in a URL.

Run locally (Mac) with the project .env loaded, e.g.:
    set -a && source .env && set +a
    pytest engine/tests/integration/test_supabase_storage_live.py -v
"""

from __future__ import annotations

import hashlib
import os

import pytest

_HAVE_CREDS = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

pytestmark = pytest.mark.skipif(
    not _HAVE_CREDS,
    reason="hosted Supabase integration: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set",
)


def _sha_path(data: bytes) -> str:
    return f"test/integration/{hashlib.sha256(data).hexdigest()}.bin"


@pytest.fixture
def supabase_store():
    from baculus.storage.supabase_store import SupabaseObjectStore

    bucket = os.environ.get("BACULUS_STORAGE_BUCKET", "baculus-raw")
    return SupabaseObjectStore(bucket=bucket)


def test_roundtrip_and_idempotency(supabase_store):
    payload = b'{"baculus":"integration","v":1}'
    path = _sha_path(payload)

    # Put (may already exist from a prior run; either way is fine).
    supabase_store.put(path, payload)
    assert supabase_store.exists(path) is True
    assert supabase_store.get(path) == payload  # byte-identical round trip

    # Identical bytes at the same content-addressed path do not duplicate.
    assert supabase_store.put(path, payload) is False


def test_storage_layer_vintaging(supabase_store):
    v1 = b'{"baculus":"vintage","close":100.00}'
    v2 = b'{"baculus":"vintage","close":101.25}'  # changed history
    p1, p2 = _sha_path(v1), _sha_path(v2)
    assert p1 != p2

    supabase_store.put(p1, v1)
    supabase_store.put(p2, v2)

    # Both vintages retrievable and distinct; neither overwrote the other.
    assert supabase_store.get(p1) == v1
    assert supabase_store.get(p2) == v2


def test_no_secret_in_object_url(supabase_store):
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    url = supabase_store._object_url("test/integration/x.bin")
    assert key not in url
