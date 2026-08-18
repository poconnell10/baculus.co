"""Shared helpers/fixtures for offline Massive adapter tests.

Builds realistic Massive stock custom-bars responses and a scripted transport so
the HTTP client, normalizer, adapter and full ingestion path are exercised with
no network. Response shape is grounded in Massive's official client (ADR-0009).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from baculus.adapters.base import SourceRequest, VendorArtifact
from baculus.adapters.massive import MassiveAdapter
from baculus.adapters.massive.aggregates import build_aggregates_envelope
from baculus.adapters.massive.http import MassiveHTTPClient
from baculus.ingestion.runner import IngestionRunner
from baculus.lineage.canonical_json import to_canonical_bytes
from baculus.models.enums import FetchMode

_ET = ZoneInfo("America/New_York")


def _et_ms(d: date) -> int:
    """Unix ms for the ET day boundary of a trading date (Massive daily convention)."""
    return int(datetime(d.year, d.month, d.day, tzinfo=_ET).timestamp() * 1000)


def _row(d, o, h, low, c, v, *, vw=None, n=None):
    row = {"t": _et_ms(d), "o": o, "h": h, "l": low, "c": c, "v": v}
    if vw is not None:
        row["vw"] = vw
    if n is not None:
        row["n"] = n
    return row


def _response(ticker, rows, *, adjusted=True, status="OK", request_id="req_test", next_url=None):
    doc = {
        "ticker": ticker,
        "adjusted": adjusted,
        "status": status,
        "request_id": request_id,
        "resultsCount": len(rows),
        "queryCount": len(rows),
        "results": rows,
    }
    if next_url is not None:
        doc["next_url"] = next_url
    return doc


def _body(doc) -> bytes:
    return json.dumps(doc).encode("utf-8")


class ScriptedTransport:
    """Returns queued (status, headers, body) tuples or raises queued exceptions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, headers_out, body = item
        return status, headers_out, body


def _envelope_artifact(
    page_docs,
    *,
    ticker="AAPL",
    date_from="2020-01-01",
    date_to="2020-01-31",
    adjusted=True,
    retrieved_at=None,
):
    """Build a VendorArtifact whose payload is a retained aggregates envelope.

    ``page_docs`` are the vendor response objects (one per fetched page); they are
    serialized to their verbatim page bodies exactly as the live client would
    retain them, then wrapped in the deterministic envelope.
    """
    endpoint = f"/v2/aggs/ticker/{ticker}/range/1/day/{date_from}/{date_to}"
    envelope = build_aggregates_envelope(
        endpoint=endpoint,
        request={
            "ticker": ticker,
            "multiplier": 1,
            "timespan": "day",
            "from": date_from,
            "to": date_to,
            "adjusted": adjusted,
            "sort": "asc",
            "limit": 50000,
        },
        adjusted=adjusted,
        pages_body_text=[json.dumps(d) for d in page_docs],
        request_ids=[d.get("request_id") for d in page_docs],
        http_status=[200 for _ in page_docs],
    )
    payload = to_canonical_bytes(envelope)
    return VendorArtifact(
        source="massive",
        dataset="stocks/daily",
        payload=payload,
        content_type="application/json",
        file_extension="json",
        fetch_mode=FetchMode.LIVE,
        retrieved_at=retrieved_at or datetime(2020, 2, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        event_date_min=date.fromisoformat(date_from),
        event_date_max=date.fromisoformat(date_to),
        row_count=sum(len(d.get("results") or []) for d in page_docs),
        request_parameters={"source": "massive"},
        vendor_metadata={"status": "OK"},
    )


@pytest.fixture
def mv():
    return SimpleNamespace(
        et_ms=_et_ms,
        row=_row,
        response=_response,
        body=_body,
        ok=lambda doc, headers=None: (200, headers or {"x-request-id": "req_test"}, _body(doc)),
        ScriptedTransport=ScriptedTransport,
        envelope_artifact=_envelope_artifact,
    )


@pytest.fixture
def massive_key(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-secret-key-XYZ")
    return "test-secret-key-XYZ"


@pytest.fixture
def aapl_request() -> SourceRequest:
    """The M0 live-proof request: AAPL daily, Jan 2020, adjusted."""
    return SourceRequest(
        dataset="stocks/daily",
        symbols=("AAPL",),
        start=date(2020, 1, 1),
        end=date(2020, 1, 31),
        options=(("adjusted", "true"),),
    )


@pytest.fixture
def make_live_runner(store, object_store, calendar, fixed_clock, massive_key):
    """Factory: a runner whose Massive adapter is in LIVE mode over a scripted
    transport (no socket). ``responses`` is the queue the transport replays."""

    def _factory(responses, *, base_url="https://api.massive.example"):
        transport = ScriptedTransport(responses)
        client = MassiveHTTPClient(
            base_url=base_url, transport=transport, sleep=lambda _s: None, max_retries=2
        )
        adapter = MassiveAdapter(
            mode=FetchMode.LIVE,
            base_url=base_url,
            calendar=calendar,
            clock=fixed_clock,
            http_client=client,
        )
        runner = IngestionRunner(
            store=store, object_store=object_store, adapter=adapter, calendar=calendar
        )
        return SimpleNamespace(runner=runner, adapter=adapter, transport=transport, client=client)

    return _factory
