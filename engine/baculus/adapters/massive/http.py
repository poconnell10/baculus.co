"""Minimal, offline-testable HTTP client for the Massive REST API.

Grounded in Massive's official Python client (``massive-com/client-python``, a
polygon-api-client fork). Evidence recorded in
``docs/decisions/ADR-0009-massive-vendor-semantics.md``:

  * Authentication: ``Authorization: Bearer <MASSIVE_API_KEY>`` header — the key
    is NEVER placed in a URL or query string, and never logged.
  * Retry set: HTTP 413, 429, 499, 500, 502, 503, 504 with exponential backoff;
    401/403 (auth) and other deterministic 4xx are NOT retried.
  * Explicit connect/read timeouts (no unbounded default).
  * Pagination: responses may carry ``next_url``; the client follows it by
    re-issuing the path+query against the base host with the same auth header.

Implemented directly with the standard library (no SDK/`requests` dependency).
The network call is isolated behind an injectable ``transport`` so the whole
retry/pagination/redaction surface is unit-tested offline without a socket.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from baculus.adapters.massive.reasons import MassiveError, MassiveReason

# Retryable HTTP statuses, per the official client's Retry configuration.
RETRYABLE_STATUSES = frozenset({413, 429, 499, 500, 502, 503, 504})
AUTH_STATUSES = frozenset({401, 403})

# (status, headers_lower, body_bytes)
TransportResult = tuple[int, dict[str, str], bytes]
Transport = Callable[[str, dict[str, str], float], TransportResult]


@dataclass(frozen=True, slots=True)
class RawPage:
    """One fetched page: the verbatim body plus provenance."""

    path_and_query: str
    status: int
    request_id: str | None
    body_text: str  # exact vendor bytes, decoded utf-8, unchanged
    doc: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MassiveHTTPClient:
    base_url: str
    api_key_env: str = "MASSIVE_API_KEY"
    timeout: float = 15.0
    max_retries: int = 3
    backoff_factor: float = 0.1
    max_pages: int = 1000
    transport: Transport | None = None
    sleep: Callable[[float], None] = time.sleep
    _default_transport_marker: bool = field(default=False, repr=False)

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise MassiveError(
                MassiveReason.MASSIVE_AUTH_FAILED,
                f"{self.api_key_env} is not set",
            )
        return key

    def _headers(self) -> dict[str, str]:
        # Secret travels ONLY here; never in a URL, never logged.
        return {"Authorization": f"Bearer {self._api_key()}", "Accept": "application/json"}

    def _transport(self) -> Transport:
        return self.transport or _urllib_transport

    # -- single request with bounded retry -----------------------------------
    def get(self, path_and_query: str) -> RawPage:
        url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path_and_query.lstrip("/"))
        transport = self._transport()
        headers = self._headers()
        attempt = 0
        while True:
            try:
                status, resp_headers, body = transport(url, headers, self.timeout)
            except TimeoutError as exc:
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    attempt += 1
                    continue
                raise MassiveError(
                    MassiveReason.MASSIVE_TIMEOUT, "request timed out", retryable=True
                ) from exc
            except (urllib.error.URLError, OSError):
                # DNS / connection failure.
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    attempt += 1
                    continue
                raise MassiveError(
                    MassiveReason.MASSIVE_CONNECTION_FAILED,
                    "connection failed",
                    retryable=True,
                ) from None

            request_id = resp_headers.get("x-request-id")
            if 200 <= status < 300:
                return self._decode(path_and_query, status, request_id, body)
            if status in AUTH_STATUSES:
                raise MassiveError(
                    MassiveReason.MASSIVE_AUTH_FAILED,
                    f"authentication failed (HTTP {status})",
                    http_status=status,
                    request_id=request_id,
                )
            if status in RETRYABLE_STATUSES and attempt < self.max_retries:
                self._backoff(attempt, resp_headers.get("retry-after"))
                attempt += 1
                continue
            if status == 429:
                raise MassiveError(
                    MassiveReason.MASSIVE_RATE_LIMITED,
                    "rate limited; retry budget exhausted",
                    retryable=True,
                    http_status=status,
                    request_id=request_id,
                )
            raise MassiveError(
                MassiveReason.MASSIVE_HTTP_ERROR,
                f"unexpected HTTP {status}",
                retryable=status in RETRYABLE_STATUSES,
                http_status=status,
                request_id=request_id,
            )

    def _decode(self, path: str, status: int, request_id: str | None, body: bytes) -> RawPage:
        try:
            text = body.decode("utf-8")
            doc = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MassiveError(
                MassiveReason.MASSIVE_MALFORMED_JSON,
                f"response body is not valid JSON: {exc}",
                http_status=status,
                request_id=request_id,
            ) from None
        if not isinstance(doc, dict):
            raise MassiveError(
                MassiveReason.MASSIVE_MALFORMED_JSON,
                "response JSON is not an object",
                http_status=status,
                request_id=request_id,
            )
        # request_id may be in the body as well as the header.
        rid = request_id or (
            doc.get("request_id") if isinstance(doc.get("request_id"), str) else None
        )
        return RawPage(path_and_query=path, status=status, request_id=rid, body_text=text, doc=doc)

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                self.sleep(min(float(retry_after), 60.0))  # respect server guidance
                return
            except ValueError:
                pass
        self.sleep(self.backoff_factor * (2**attempt))

    # -- pagination (only followed when the endpoint returns next_url) --------
    def get_paginated(self, path_and_query: str) -> list[RawPage]:
        pages: list[RawPage] = []
        current = path_and_query
        for _ in range(self.max_pages):
            page = self.get(current)
            pages.append(page)
            next_url = page.doc.get("next_url")
            if not next_url or not isinstance(next_url, str):
                return pages
            # Follow ONLY the vendor-provided continuation; use its path+query.
            parts = urllib.parse.urlsplit(next_url)
            current = parts.path + (f"?{parts.query}" if parts.query else "")
        raise MassiveError(
            MassiveReason.MASSIVE_HTTP_ERROR,
            f"pagination exceeded max_pages={self.max_pages}",
        )


def _urllib_transport(url: str, headers: dict[str, str], timeout: float) -> TransportResult:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, resp.read()
    except urllib.error.HTTPError as exc:
        hdrs = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, hdrs, exc.read()


__all__ = ["RETRYABLE_STATUSES", "MassiveHTTPClient", "RawPage"]
