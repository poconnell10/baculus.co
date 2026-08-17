"""Decimal price policy: exactness and round-trip behavior.

Proves prices are fixed-precision Decimal end to end and never pass through
binary floating point, at the canonical and persistence (Parquet) boundaries.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal

import polars as pl
import pytest

from baculus.adapters.base import SourceRequest, VendorArtifact
from baculus.adapters.massive.normalize import MassiveParseError, massive_payload_to_bars
from baculus.lineage.canonical_json import digest_canonical
from baculus.models.bar import PRICE_DTYPE
from baculus.models.enums import FetchMode
from baculus.normalization.canonical import build_canonical_frame

_OBS = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)


def _artifact(payload: bytes) -> VendorArtifact:
    return VendorArtifact(
        source="massive",
        dataset="stocks/daily",
        payload=payload,
        content_type="application/json",
        file_extension="json",
        fetch_mode=FetchMode.FIXTURE,
        retrieved_at=_OBS,
        event_date_min=date(2024, 1, 2),
        event_date_max=date(2024, 1, 2),
        row_count=1,
    )


def test_price_is_exact_decimal_not_float():
    # 1.005 is the classic binary-float rounding trap (round(1.005, 2) == 1.0).
    payload = (
        b'{"results":[{"T":"SPY","t":1704153600000,"o":1.005,"h":1.01,"l":1.00,"c":1.005,"v":10}]}'
    )
    bars = massive_payload_to_bars(_artifact(payload))
    assert bars[0].open == Decimal("1.005")
    assert bars[0].close == Decimal("1.005")
    # Exact fixed-scale representation, no float artifacts.
    assert bars[0].open == Decimal("1.00500000")


def test_over_precision_is_rejected_not_rounded():
    # 9 fractional digits exceeds the fixed scale (8) -> fail closed.
    payload = (
        b'{"results":[{"T":"SPY","t":1704153600000,"o":1.234567891,"h":2,"l":1,"c":2,"v":10}]}'
    )
    with pytest.raises(MassiveParseError):
        massive_payload_to_bars(_artifact(payload))


def test_canonical_frame_uses_decimal_dtype():
    payload = (
        b'{"results":[{"T":"SPY","t":1704153600000,"o":100.1,"h":101.2,"l":99.9,"c":100.5,"v":10}]}'
    )
    df = build_canonical_frame(massive_payload_to_bars(_artifact(payload)))
    for col in ("open", "high", "low", "close"):
        assert df.schema[col] == PRICE_DTYPE


def test_parquet_roundtrip_preserves_exact_decimal():
    payload = (
        b'{"results":[{"T":"SPY","t":1704153600000,'
        b'"o":123.4567,"h":130.0,"l":120.0,"c":128.25,"v":10}]}'
    )
    df = build_canonical_frame(massive_payload_to_bars(_artifact(payload)))
    buf = io.BytesIO()
    df.write_parquet(buf)
    back = pl.read_parquet(io.BytesIO(buf.getvalue()))
    assert back.schema["open"] == PRICE_DTYPE
    assert back["open"].to_list()[0] == Decimal("123.4567")
    assert back["close"].to_list()[0] == Decimal("128.25")


def test_canonical_json_decimal_is_deterministic():
    a = {"price": Decimal("123.4567")}
    b = {"price": Decimal("123.45670000")}  # numerically equal, different scale
    # Canonical serialization normalizes the Decimal, so equal value -> equal hash.
    assert digest_canonical(a) == digest_canonical(b)
    assert digest_canonical({"price": Decimal("123.4568")}) != digest_canonical(a)


def test_ingested_canonical_parquet_is_decimal(make_runner, store, object_store):
    """End-to-end: the persisted canonical parquet stores Decimal prices."""
    req = SourceRequest(
        dataset="stocks/daily",
        symbols=("SPY", "XLK", "XLE"),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
    )
    result = make_runner().ingest(req)
    row = store.connection.execute(
        "SELECT object_path FROM dataset_artifacts WHERE dataset_build_id = %s"
        " AND kind = 'canonical_parquet'",
        (result.dataset_build_id,),
    ).fetchone()
    df = pl.read_parquet(io.BytesIO(object_store.get(row["object_path"])))
    assert df.schema["open"] == PRICE_DTYPE
    assert df.schema["close"] == PRICE_DTYPE
