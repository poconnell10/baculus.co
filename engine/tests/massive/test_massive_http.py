"""Offline tests for the Massive HTTP client (no network)."""

from __future__ import annotations

import urllib.error
from datetime import date

import pytest

from baculus.adapters.massive.http import MassiveHTTPClient
from baculus.adapters.massive.reasons import MassiveError, MassiveReason

_BASE = "https://api.massive.example"


def _client(transport, **kw):
    kw.setdefault("max_retries", 2)
    return MassiveHTTPClient(base_url=_BASE, transport=transport, sleep=lambda _s: None, **kw)


def test_success_returns_doc_and_request_id(mv, massive_key):
    doc = mv.response("AAPL", [mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 100)])
    t = mv.ScriptedTransport([mv.ok(doc, {"x-request-id": "req-1"})])
    page = _client(t).get("/v2/aggs/x")
    assert page.status == 200
    assert page.request_id == "req-1"
    assert page.doc["ticker"] == "AAPL"


def test_missing_api_key_is_auth_failed(monkeypatch, mv):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    t = mv.ScriptedTransport([mv.ok(mv.response("AAPL", []))])
    with pytest.raises(MassiveError) as ei:
        _client(t).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_AUTH_FAILED
    assert t.calls == []  # never even attempted the request


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_not_retried(mv, massive_key, status):
    t = mv.ScriptedTransport([(status, {}, b"{}"), (status, {}, b"{}")])
    with pytest.raises(MassiveError) as ei:
        _client(t).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_AUTH_FAILED
    assert len(t.calls) == 1  # no retry on auth


def test_rate_limited_retries_then_succeeds(mv, massive_key):
    doc = mv.response("AAPL", [])
    t = mv.ScriptedTransport([(429, {"retry-after": "0"}, b"{}"), mv.ok(doc)])
    page = _client(t).get("/x")
    assert page.status == 200
    assert len(t.calls) == 2


def test_rate_limited_exhausts_to_reason(mv, massive_key):
    t = mv.ScriptedTransport([(429, {}, b"{}")] * 5)
    with pytest.raises(MassiveError) as ei:
        _client(t, max_retries=2).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_RATE_LIMITED
    assert len(t.calls) == 3  # initial + 2 retries, then give up


def test_server_error_retries_then_fails(mv, massive_key):
    t = mv.ScriptedTransport([(500, {}, b"{}")] * 5)
    with pytest.raises(MassiveError) as ei:
        _client(t, max_retries=2).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_HTTP_ERROR
    assert ei.value.http_status == 500


def test_client_4xx_not_retried(mv, massive_key):
    t = mv.ScriptedTransport([(400, {}, b"{}"), (400, {}, b"{}")])
    with pytest.raises(MassiveError) as ei:
        _client(t).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_HTTP_ERROR
    assert len(t.calls) == 1


def test_timeout_retries_then_reason(mv, massive_key):
    t = mv.ScriptedTransport([TimeoutError(), TimeoutError(), TimeoutError()])
    with pytest.raises(MassiveError) as ei:
        _client(t, max_retries=2).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_TIMEOUT
    assert len(t.calls) == 3


def test_connection_failure_is_reason(mv, massive_key):
    t = mv.ScriptedTransport([urllib.error.URLError("dns"), urllib.error.URLError("dns")])
    with pytest.raises(MassiveError) as ei:
        _client(t, max_retries=1).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_CONNECTION_FAILED


def test_malformed_json_is_reason(mv, massive_key):
    t = mv.ScriptedTransport([(200, {}, b"{not json")])
    with pytest.raises(MassiveError) as ei:
        _client(t).get("/x")
    assert ei.value.reason is MassiveReason.MASSIVE_MALFORMED_JSON


def test_pagination_follows_next_url(mv, massive_key):
    p1 = mv.response(
        "AAPL",
        [mv.row(date(2020, 1, 2), 1, 2, 0.5, 1.5, 100)],
        next_url="https://api.massive.example/v2/aggs/x?cursor=abc",
    )
    p2 = mv.response("AAPL", [mv.row(date(2020, 1, 3), 1, 2, 0.5, 1.5, 100)])
    t = mv.ScriptedTransport([mv.ok(p1), mv.ok(p2)])
    pages = _client(t).get_paginated("/v2/aggs/x")
    assert len(pages) == 2
    # The second request followed the vendor cursor path+query.
    assert "cursor=abc" in t.calls[1][0]


def test_secret_only_in_header_never_in_url(mv, massive_key):
    t = mv.ScriptedTransport([mv.ok(mv.response("AAPL", []))])
    _client(t).get("/v2/aggs/x")
    url, headers, _timeout = t.calls[0]
    assert massive_key not in url
    assert headers["Authorization"] == f"Bearer {massive_key}"
