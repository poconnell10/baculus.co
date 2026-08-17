"""Supabase Storage implementation of the ObjectStore interface.

This is the hosted-POC object store. It satisfies the same ``ObjectStore``
contract as ``LocalObjectStore`` and preserves the content-addressed invariant:
because object paths embed the SHA-256, an upload that collides is treated as
"already present" and never overwrites bytes.

Security: the service-role key is read from the environment, sent only in the
Authorization/apikey headers, and never logged. It must never reach a browser.

Live verification status: this talks to the documented Supabase Storage v1 REST
API. It is exercised against a real Supabase project only when credentials are
provided (see scripts/verify_stack.py); it is NOT part of the offline test proof.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

from baculus.storage.object_store import ObjectStore


class SupabaseStorageError(RuntimeError):
    pass


class SupabaseObjectStore(ObjectStore):
    def __init__(
        self,
        *,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        bucket: str = "baculus-raw",
        timeout: float = 60.0,
    ) -> None:
        url = supabase_url or os.environ.get("SUPABASE_URL")
        key = service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise SupabaseStorageError(
                "SupabaseObjectStore requires SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY (server-only; never expose to a browser)."
            )
        self._base = f"{url.rstrip('/')}/storage/v1"
        self._key = key
        self._bucket = bucket
        self._timeout = timeout

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        # Secret travels only here; it is never logged or echoed.
        headers = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        if extra:
            headers.update(extra)
        return headers

    def _object_url(self, path: str) -> str:
        return f"{self._base}/object/{self._bucket}/{path.lstrip('/')}"

    def exists(self, path: str) -> bool:
        info_url = f"{self._base}/object/info/{self._bucket}/{path.lstrip('/')}"
        req = urllib.request.Request(info_url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise SupabaseStorageError(f"exists() failed: HTTP {exc.code}") from None

    def put(self, path: str, data: bytes) -> bool:
        # Create-only (no upsert): content addressing means a collision is the
        # same bytes, so a 409 means "already stored" -> not newly written.
        req = urllib.request.Request(
            self._object_url(path),
            data=data,
            headers=self._headers(
                {"Content-Type": "application/octet-stream", "x-upsert": "false"}
            ),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return False
            raise SupabaseStorageError(f"put() failed: HTTP {exc.code}") from None

    def get(self, path: str) -> bytes:
        req = urllib.request.Request(self._object_url(path), headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise SupabaseStorageError(f"get() failed: HTTP {exc.code}") from None


__all__ = ["SupabaseObjectStore", "SupabaseStorageError"]
