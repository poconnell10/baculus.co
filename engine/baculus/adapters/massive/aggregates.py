"""Massive stock custom-bars (aggregates) contract: envelope + normalization.

Grounded in Massive's official API / client (see ADR-0009):

  * Endpoint: ``/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}``
    with query ``adjusted``, ``sort``, ``limit`` (limit default 5000, max 50000).
  * Response envelope keys: ``ticker``, ``adjusted``, ``status``, ``request_id``,
    ``resultsCount``/``queryCount``/``count``, ``results``, optional ``next_url``.
  * Per-result keys: ``t`` (Unix ms, ET day boundary), ``o``/``h``/``l``/``c``
    (OHLC), ``v`` (volume), ``vw`` (VWAP), ``n`` (transaction count).

We retain every fetched page verbatim inside a deterministic envelope so the
raw evidence is reconstructable and content-addressable, then normalize into the
existing vendor-neutral canonical daily bar. Vendor/identity/integrity problems
fail closed with a ``MASSIVE_*`` reason BEFORE any canonical propagation.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from baculus.adapters.base import VendorArtifact
from baculus.adapters.massive.reasons import MassiveError, MassiveReason
from baculus.models.bar import PRICE_DECIMAL_SCALE, CanonicalDailyBar

ENVELOPE_KIND = "baculus.massive.aggregates/1"
_EASTERN = ZoneInfo("America/New_York")
_QUANTUM = Decimal(1).scaleb(-PRICE_DECIMAL_SCALE)
_OK_STATUSES = frozenset({"OK", "DELAYED"})


def build_aggregates_envelope(
    *,
    endpoint: str,
    request: dict[str, Any],
    adjusted: bool,
    pages_body_text: list[str],
    request_ids: list[str | None],
    http_status: list[int],
) -> dict[str, Any]:
    """Deterministic envelope retaining every page's verbatim body + provenance."""
    return {
        "envelope_kind": ENVELOPE_KIND,
        "vendor": "massive",
        "endpoint": endpoint,
        "endpoint_version": "v2",
        "http_method": "GET",
        "request": request,
        "adjusted": adjusted,
        "request_ids": [r for r in request_ids if r],
        "http_status": http_status,
        # Exact vendor bytes per page (decoded utf-8, unchanged text).
        "pages": pages_body_text,
    }


def _business_date(t_ms: int) -> date:
    # Massive/Polygon daily aggregate timestamps are ET day boundaries. The
    # canonical business date is the Eastern-time calendar date, NOT a UTC
    # truncation. (Confirmed against the live AAPL proof — ADR-0009.)
    return datetime.fromtimestamp(t_ms / 1000, tz=UTC).astimezone(_EASTERN).date()


def _require_finite_price(value: Any, *, request_id: str | None) -> Decimal:
    if isinstance(value, bool):
        raise MassiveError(MassiveReason.MASSIVE_NUMERIC_NONFINITE, "price is a bool")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MassiveError(
                MassiveReason.MASSIVE_NUMERIC_NONFINITE,
                f"non-finite price {value!r}",
                request_id=request_id,
            )
        d = Decimal(str(value))
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise MassiveError(
                MassiveReason.MASSIVE_NUMERIC_NONFINITE,
                "non-finite Decimal price",
                request_id=request_id,
            )
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    else:
        raise MassiveError(
            MassiveReason.MASSIVE_NUMERIC_NONFINITE,
            f"non-numeric price {type(value).__name__}",
            request_id=request_id,
        )
    try:
        return d.quantize(_QUANTUM)
    except InvalidOperation:
        raise MassiveError(
            MassiveReason.MASSIVE_NUMERIC_NONFINITE,
            "price exceeds fixed precision",
            request_id=request_id,
        ) from None


def _require_volume(value: Any, *, request_id: str | None) -> int:
    if isinstance(value, bool):
        raise MassiveError(MassiveReason.MASSIVE_VOLUME_INVALID, "volume is a bool")
    if isinstance(value, int):
        vol = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise MassiveError(
                MassiveReason.MASSIVE_VOLUME_INVALID,
                f"volume not integral {value!r}",
                request_id=request_id,
            )
        vol = int(value)
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise MassiveError(
                MassiveReason.MASSIVE_VOLUME_INVALID,
                "volume not integral",
                request_id=request_id,
            )
        vol = int(value)
    else:
        raise MassiveError(
            MassiveReason.MASSIVE_VOLUME_INVALID,
            f"non-numeric volume {type(value).__name__}",
            request_id=request_id,
        )
    if vol < 0:
        raise MassiveError(
            MassiveReason.MASSIVE_VOLUME_INVALID, f"negative volume {vol}", request_id=request_id
        )
    return vol


def massive_aggregates_to_bars(artifact: VendorArtifact) -> list[CanonicalDailyBar]:
    """Parse a retained Massive aggregates envelope into canonical bars.

    Fails closed with a ``MASSIVE_*`` reason on any vendor/identity/integrity
    problem, before any canonical propagation.
    """
    try:
        envelope = json.loads(artifact.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MassiveError(
            MassiveReason.MASSIVE_MALFORMED_JSON, f"envelope not JSON: {exc}"
        ) from None

    request = envelope.get("request", {})
    expected_ticker = str(request.get("ticker", "")).strip().upper()
    req_from = _parse_iso(request.get("from"))
    req_to = _parse_iso(request.get("to"))

    bars: list[CanonicalDailyBar] = []
    seen_dates: set[date] = set()
    for page_text in envelope.get("pages", []):
        # parse_float=Decimal preserves the vendor's exact textual price.
        try:
            page = json.loads(page_text, parse_float=Decimal)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MassiveError(
                MassiveReason.MASSIVE_MALFORMED_JSON, f"page not JSON: {exc}"
            ) from None

        status = page.get("status")
        if status is not None and status not in _OK_STATUSES:
            raise MassiveError(
                MassiveReason.MASSIVE_UNEXPECTED_STATUS,
                f"vendor status {status!r}",
                request_id=page.get("request_id"),
            )
        rid = page.get("request_id") if isinstance(page.get("request_id"), str) else None
        page_ticker = str(page.get("ticker", "")).strip().upper()
        if expected_ticker and page_ticker and page_ticker != expected_ticker:
            raise MassiveError(
                MassiveReason.MASSIVE_TICKER_MISMATCH,
                f"expected {expected_ticker}, got {page_ticker}",
                request_id=rid,
            )

        results = page.get("results")
        if results is None:  # vendor-declared no data is distinct from malformed
            continue
        if not isinstance(results, list):
            raise MassiveError(
                MassiveReason.MASSIVE_MALFORMED_JSON, "'results' is not a list", request_id=rid
            )

        for row in results:
            if not isinstance(row, dict):
                raise MassiveError(
                    MassiveReason.MASSIVE_MALFORMED_JSON,
                    "result row is not an object",
                    request_id=rid,
                )
            t = row.get("t")
            if not isinstance(t, int) or isinstance(t, bool):
                raise MassiveError(
                    MassiveReason.MASSIVE_TIMESTAMP_INVALID,
                    f"invalid timestamp {t!r}",
                    request_id=rid,
                )
            event_date = _business_date(t)
            if req_from and req_to and not (req_from <= event_date <= req_to):
                raise MassiveError(
                    MassiveReason.MASSIVE_OUTSIDE_REQUEST_RANGE,
                    f"{event_date} outside [{req_from}, {req_to}]",
                    request_id=rid,
                )
            if event_date in seen_dates:
                raise MassiveError(
                    MassiveReason.MASSIVE_BUSINESS_DATE_DUPLICATE,
                    f"duplicate business date {event_date}",
                    request_id=rid,
                )
            seen_dates.add(event_date)

            o = _require_finite_price(row.get("o"), request_id=rid)
            h = _require_finite_price(row.get("h"), request_id=rid)
            low = _require_finite_price(row.get("l"), request_id=rid)
            c = _require_finite_price(row.get("c"), request_id=rid)
            v = _require_volume(row.get("v"), request_id=rid)
            if not (h >= low and low <= o <= h and low <= c <= h):
                raise MassiveError(
                    MassiveReason.MASSIVE_OHLC_INVALID,
                    f"o={o},h={h},l={low},c={c}",
                    request_id=rid,
                )

            bars.append(
                CanonicalDailyBar(
                    symbol=expected_ticker or page_ticker,
                    event_date=event_date,
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=v,
                    source=artifact.source,
                    observation_time=artifact.retrieved_at,
                    source_artifact_id=artifact.artifact_id,
                )
            )
    return bars


def _parse_iso(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["ENVELOPE_KIND", "build_aggregates_envelope", "massive_aggregates_to_bars"]
