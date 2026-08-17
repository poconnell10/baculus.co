"""Deterministic manifest serialization.

Covers required tests:
 12. deterministic manifest serialization produces the same hash for the same content
 13. changed manifest content changes the hash
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from baculus.lineage.canonical_json import digest_canonical
from baculus.lineage.manifest import build_dataset_manifest, manifest_digest

_KW = {
    "source": "massive",
    "dataset": "stocks/daily",
    "logical_key": "massive|stocks/daily|2024-01-02|2024-03-28",
    "vintage": 1,
    "state": "PROVISIONAL",
    "symbols": ("XLK", "SPY"),  # deliberately unsorted; manifest sorts them
    "event_date_min": date(2024, 1, 2),
    "event_date_max": date(2024, 3, 28),
    "observation_time": datetime(2024, 4, 1, 12, 0, tzinfo=UTC),
    "raw_sha256": "a" * 64,
    "raw_object_path": "raw/massive/stocks/daily/observation_date=2024-04-01/x.json",
    "raw_byte_size": 1234,
    "raw_row_count": 183,
    "fetch_mode": "fixture",
    "canonical_schema_version": "1.0.0",
    "canonical_row_count": 183,
    "canonical_columns": ("symbol", "event_date", "open"),
    "validation_summary": {"passed": True, "error_count": 0, "warning_count": 0},
    "calendar_descriptor": {"calendar_name": "XNYS", "calendar_library_version": "4.13.2"},
}


def test_same_content_same_hash():
    """Required test #12."""
    m1 = build_dataset_manifest(**_KW)
    m2 = build_dataset_manifest(**_KW)
    assert manifest_digest(m1) == manifest_digest(m2)

    # Key ordering must not matter — canonical serialization sorts keys.
    reordered = {k: m1[k] for k in reversed(list(m1.keys()))}
    assert digest_canonical(reordered) == manifest_digest(m1)


def test_changed_content_changes_hash():
    """Required test #13."""
    base = build_dataset_manifest(**_KW)
    base_digest = manifest_digest(base)

    changed_kw = dict(_KW)
    changed_kw["vintage"] = 2
    assert manifest_digest(build_dataset_manifest(**changed_kw)) != base_digest

    # A different observation time is a different manifest by design.
    changed_obs = dict(_KW)
    changed_obs["observation_time"] = datetime(2024, 4, 2, 12, 0, tzinfo=UTC)
    assert manifest_digest(build_dataset_manifest(**changed_obs)) != base_digest


def test_digest_is_sha256_hex():
    digest = manifest_digest(build_dataset_manifest(**_KW))
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
