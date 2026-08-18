"""The Massive market-data adapter (daily U.S. equity bars).

Two fetch modes:
  * ``FIXTURE`` — deterministic, offline, reproducible. Used for the synthetic
    M0 proof cohort (SPY/XLK/XLE) when live vendor access is unavailable. Output
    is byte-identical for identical inputs.
  * ``LIVE`` — calls the real Massive REST API using ``MASSIVE_API_KEY`` (via the
    header-only, redacting :class:`MassiveHTTPClient`). It fetches the stock
    custom-bars (aggregates) endpoint for a single ticker/range and retains every
    page verbatim. Endpoints, auth, fields and pagination are grounded in
    Massive's official client (see ADR-0009).

The adapter returns the vendor payload verbatim; it makes no idempotency,
vintaging, storage, or authority decisions.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime

from baculus.adapters.base import MarketDataSourceAdapter, SourceRequest, VendorArtifact
from baculus.adapters.massive.aggregates import (
    ENVELOPE_KIND,
    build_aggregates_envelope,
    massive_aggregates_to_bars,
)
from baculus.adapters.massive.fixtures.generator import OverrideMap, build_massive_payload
from baculus.adapters.massive.http import MassiveHTTPClient
from baculus.adapters.massive.normalize import massive_payload_to_bars
from baculus.adapters.massive.reasons import MassiveError, MassiveReason
from baculus.lineage.canonical_json import to_canonical_bytes
from baculus.models.bar import CanonicalDailyBar
from baculus.models.enums import FetchMode
from baculus.reference.calendar import TradingCalendar

SOURCE_ID = "massive"
# Base host from Massive's official REST client; overridable via MASSIVE_BASE_URL.
# The exact host is re-confirmed on the first live call (ADR-0009).
DEFAULT_BASE_URL = "https://api.massive.com"
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
        fixture_nonce: str | None = None,
        http_client: MassiveHTTPClient | None = None,
        api_key_env: str = _ENV_API_KEY,
    ) -> None:
        self._mode = mode
        self._base_url = base_url or os.environ.get("MASSIVE_BASE_URL") or DEFAULT_BASE_URL
        self._calendar = calendar or TradingCalendar("XNYS")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fixture_overrides = fixture_overrides or {}
        # Optional per-run request id emitted into fixture bytes so otherwise
        # identical proof runs can be isolated. FIXTURE mode only; never used live.
        self._fixture_nonce = fixture_nonce
        self._api_key_env = api_key_env
        self._http = http_client  # injectable for offline tests

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    def _client(self) -> MassiveHTTPClient:
        if self._http is not None:
            return self._http
        return MassiveHTTPClient(base_url=self._base_url, api_key_env=self._api_key_env)

    # -- fetch ---------------------------------------------------------------
    def fetch(self, request: SourceRequest) -> VendorArtifact:
        retrieved_at = self._clock()
        if self._mode is FetchMode.FIXTURE:
            return self._fetch_fixture(request, retrieved_at)
        return self._fetch_live(request, retrieved_at)

    def _fetch_fixture(self, request: SourceRequest, retrieved_at: datetime) -> VendorArtifact:
        symbols = request.normalized_symbols()
        sessions = self._calendar.sessions_in_range(request.start, request.end)
        payload_doc = build_massive_payload(
            symbols, sessions, overrides=self._fixture_overrides, nonce=self._fixture_nonce
        )
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
        symbols = request.normalized_symbols()
        if len(symbols) != 1:
            raise MassiveError(
                MassiveReason.MASSIVE_HTTP_ERROR,
                "live aggregates fetch is single-ticker; request exactly one symbol",
            )
        ticker = symbols[0]
        adjusted = self._adjusted(request)
        multiplier, timespan = 1, "day"
        path = (
            f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/"
            f"{request.start.isoformat()}/{request.end.isoformat()}"
            f"?adjusted={'true' if adjusted else 'false'}&sort=asc&limit=50000"
        )
        pages = self._client().get_paginated(path)
        envelope = build_aggregates_envelope(
            endpoint=(
                f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/"
                f"{request.start.isoformat()}/{request.end.isoformat()}"
            ),
            request={
                "ticker": ticker,
                "multiplier": multiplier,
                "timespan": timespan,
                "from": request.start.isoformat(),
                "to": request.end.isoformat(),
                "adjusted": adjusted,
                "sort": "asc",
                "limit": 50000,
            },
            adjusted=adjusted,
            pages_body_text=[p.body_text for p in pages],
            request_ids=[p.request_id for p in pages],
            http_status=[p.status for p in pages],
        )
        payload = to_canonical_bytes(envelope)
        row_count = sum(
            len(p.doc.get("results") or []) for p in pages if isinstance(p.doc.get("results"), list)
        )
        first_rid = next((p.request_id for p in pages if p.request_id), "")
        return VendorArtifact(
            source=SOURCE_ID,
            dataset=request.dataset,
            payload=payload,  # deterministic envelope retaining every page verbatim
            content_type="application/json",
            file_extension="json",
            fetch_mode=FetchMode.LIVE,
            retrieved_at=retrieved_at,
            event_date_min=request.start,
            event_date_max=request.end,
            row_count=row_count,
            request_parameters=self.describe_request(request),
            vendor_metadata={
                "status": "OK",
                "adjusted": "true" if adjusted else "false",
                "pages": str(len(pages)),
                "request_id": first_rid or "",
            },
        )

    def reference_lookup(self, ticker: str) -> dict:
        """Look up a single ticker's reference record (``/v3/reference/tickers``).

        Returns the vendor ``results`` object; raises ``MassiveError`` with
        ``MASSIVE_TICKER_MISMATCH`` if the returned ticker does not match.
        """
        symbol = ticker.strip().upper()
        page = self._client().get(f"/v3/reference/tickers/{symbol}")
        results = page.doc.get("results")
        if isinstance(results, list):  # some deployments return a list
            results = results[0] if results else {}
        if not isinstance(results, dict):
            raise MassiveError(
                MassiveReason.MASSIVE_MALFORMED_JSON,
                "reference response missing 'results' object",
                request_id=page.request_id,
            )
        returned = str(results.get("ticker", "")).strip().upper()
        if returned and returned != symbol:
            raise MassiveError(
                MassiveReason.MASSIVE_TICKER_MISMATCH,
                f"reference returned {returned}, expected {symbol}",
                request_id=page.request_id,
            )
        return results

    # -- parse / describe ----------------------------------------------------
    def parse(self, artifact: VendorArtifact) -> list[CanonicalDailyBar]:
        if self._is_aggregates_envelope(artifact.payload):
            return massive_aggregates_to_bars(artifact)
        return massive_payload_to_bars(artifact)

    @staticmethod
    def _is_aggregates_envelope(payload: bytes) -> bool:
        if b'"envelope_kind"' not in payload[:512]:
            return False
        try:
            doc = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(doc, dict) and doc.get("envelope_kind") == ENVELOPE_KIND

    @staticmethod
    def _adjusted(request: SourceRequest) -> bool:
        for key, value in request.options:
            if key == "adjusted":
                return value.strip().lower() in {"true", "1", "yes"}
        return True  # Massive default for adjusted aggregates

    def describe_request(self, request: SourceRequest) -> dict[str, str]:
        # Log-safe: contains no secrets.
        return {
            "source": SOURCE_ID,
            "dataset": request.dataset,
            "symbols": ",".join(request.normalized_symbols()),
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "multiplier": "1",
            "timespan": "day",
            "adjusted": "true" if self._adjusted(request) else "false",
            "sort": "asc",
        }


__all__ = ["SOURCE_ID", "MassiveAdapter"]
