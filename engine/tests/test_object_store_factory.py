"""Object-store selection and Supabase fail-closed behavior (offline)."""

from __future__ import annotations

import pytest

from baculus.storage import (
    LocalObjectStore,
    SupabaseObjectStore,
    SupabaseStorageError,
    object_store_from_env,
)


def test_factory_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("BACULUS_OBJECT_STORE", raising=False)
    monkeypatch.setenv("BACULUS_DATA_ROOT", str(tmp_path))
    store = object_store_from_env()
    assert isinstance(store, LocalObjectStore)


def test_supabase_requires_credentials(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    # Fail closed: no silent fallback when Supabase is explicitly requested.
    with pytest.raises(SupabaseStorageError):
        SupabaseObjectStore()
    monkeypatch.setenv("BACULUS_OBJECT_STORE", "supabase")
    with pytest.raises(SupabaseStorageError):
        object_store_from_env()


def test_supabase_store_never_puts_secret_in_url(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "super-secret-key")
    store = SupabaseObjectStore()
    url = store._object_url("raw/massive/x.json")
    assert "super-secret-key" not in url
    assert url.startswith("https://example.supabase.co/storage/v1/object/")
    # The secret lives only in headers.
    assert store._headers()["Authorization"] == "Bearer super-secret-key"
