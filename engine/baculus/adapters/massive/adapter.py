"""The Massive market-data adapter (daily U.S. equity bars).

Two fetch modes:
  * ``FIXTURE`` — deterministic, offline, reproducible. Used for M0 proof when
    live vendor access is unavailable. Output is byte-identical for identical
    inputs, which is what makes the idempotency/vintage proofs possible.
  * ``LIVE`` — calls the Massive HTTP API using ``MASSIVE_API_KEY`` from the
    environment. The key is read at call time and never logged or echoed into
    request metadata.

The adapter returns the vendor payload verbatim; it makes no idempotency,
vintaging, storage, or authority decisions.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from baculus.adapters.base import (
    MarketDataSourceAdapter,
    SourceRequest,
    VendorArtifact,
)
from baculus.adapters.massive.fixtures.generator import OverrideMap, build_massive_payload
from baculus.adapters.massive.normalize import massive_payload_to_bars
from baculus.lineage.canonical_json import to_canonical_bytes
from baculus.models.bar import CanonicalDailyBar
from baculus.models.enums import FetchMode
from baculus.reference.calendar import TradingCalendar

SOURCE_ID = "massive"
DEFAULT_BASE_URL = "https://api.massive.example/v1"
_ENV_API_KEY = "MASSIVE_API_KEY"


class MassiveAdapter(MarketDataSourceAdapter):
    def __init__(
        self,
        *,
        mode: FetchMode = FetchMode.FIXTURE,
        base_url: str | None = None,
        calendar: TradingCalendar | None = None,
        clock: Callable[[], datetime] | None = None,
        fixture_overrides: OverrideMap | None = None,
        api_key_env: str = _ENV_API_KEY,
    ) -> None:
        self._mode = mode
        self._base_url = base_url or os.environ.get("MASSIVE_BASE_URL") or DEFAULT_BASE_URL
        self._calendar = calendar or TradingCalendar("XNYS")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fixture_overrides = fixture_overrides or {}
        self._api_key_env = api_key_env

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    # -- fetch ---------------------------------------------------------------
    def fetch(self, request: SourceRequest) -> VendorArtifact:
        retrieved_at = self._clock()
        if self._mode is FetchMode.FIXTURE:
            return self._fetch_fixture(request, retrieved_at)
        return self._fetch_live(request, retrieved_at)

    def _fetch_fixture(self, request: SourceRequest, retrieved_at: datetime) -> VendorArtifact:
        symbols = request.normalized_symbols()
        sessions = self._calendar.sessions_in_range(request.start, request.end)
        payload_doc = build_massive_payload(symbols, sessions, overrides=self._fixture_overrides)
        payload = to_canonical_bytes(payload_doc)
        results = payload_doc["results"]
        assert isinstance(results, list)
        return VendorArtifact(
            source=SOURCE_ID,
            dataset=request.dataset,
            payload=payload,
            content_type="application/json",
            file_extension="json",
            fetch_mode=FetchMode.FIXTURE,
            retrieved_at=retrieved_at,
            event_date_min=sessions[0] if sessions else request.start,
            event_date_max=sessions[-1] if sessions else request.end,
            row_count=len(results),
            request_parameters=self.describe_request(request),
            vendor_metadata={"status": "OK", "mode": "fixture"},
        )

    def _fetch_live(self, request: SourceRequest, retrieved_at: datetime) -> VendorArtifact:
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise RuntimeError(
                f"live Massive fetch requires {self._api_key_env}; none set. "
                "Set the key or use FetchMode.FIXTURE."
            )
        # Vendor-neutral request params only; the API key travels in the header,
        # never in the URL or any logged/stored structure.
        query = urllib.parse.urlencode(
            {
                "symbols": ",".join(request.normalized_symbols()),
                "from": request.start.isoformat(),
                "to": request.end.isoformat(),
                "adjusted": "false",
            }
        )
        url = f"{self._base_url}/{request.dataset}?{query}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()

        doc = json.loads(payload.decode("utf-8"))
        results = doc.get("results", []) if isinstance(doc, dict) else []
        row_count = len(results) if isinstance(results, list) else None
        return VendorArtifact(
            source=SOURCE_ID,
            dataset=request.dataset,
            payload=payload,  # verbatim vendor bytes
            content_type=resp.headers.get_content_type() if resp else "application/json",
            file_extension="json",
            fetch_mode=FetchMode.LIVE,
            retrieved_at=retrieved_at,
            event_date_min=request.start,
            event_date_max=request.end,
            row_count=row_count,
            request_parameters=self.describe_request(request),
            vendor_metadata={"status": str(doc.get("status", "")) if isinstance(doc, dict) else ""},
        )

    # -- parse / describe ----------------------------------------------------
    def parse(self, artifact: VendorArtifact) -> list[CanonicalDailyBar]:
        return massive_payload_to_bars(artifact)

    def describe_request(self, request: SourceRequest) -> dict[str, str]:
        # Log-safe: contains no secrets.
        return {
            "source": SOURCE_ID,
            "dataset": request.dataset,
            "symbols": ",".join(request.normalized_symbols()),
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "adjusted": "false",
        }


__all__ = ["SOURCE_ID", "MassiveAdapter"]
