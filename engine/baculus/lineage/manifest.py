"""Deterministic dataset / governance manifests.

A manifest is a canonical description of a dataset build. Because it is
serialized canonically (see :mod:`baculus.lineage.canonical_json`), the same
logical content always yields the same SHA-256 digest. That digest is the anchor
that can be periodically committed into GitHub as a *secondary* tamper-evidence
record in a separate trust domain (see :mod:`baculus.lineage.anchor`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from baculus.lineage.canonical_json import digest_canonical

MANIFEST_SCHEMA = "baculus.dataset_manifest/1"


def build_dataset_manifest(
    *,
    source: str,
    dataset: str,
    logical_key: str,
    vintage: int,
    state: str,
    symbols: tuple[str, ...],
    event_date_min: date,
    event_date_max: date,
    observation_time: datetime,
    raw_sha256: str,
    raw_object_path: str,
    raw_byte_size: int,
    raw_row_count: int | None,
    fetch_mode: str,
    canonical_schema_version: str,
    canonical_row_count: int,
    canonical_columns: tuple[str, ...],
    validation_summary: dict[str, Any],
    calendar_descriptor: dict[str, str],
) -> dict[str, Any]:
    """Assemble a canonical, deterministic dataset manifest.

    Deliberately excludes non-deterministic fields (run ids, wall-clock created
    timestamps). ``observation_time`` IS included: it is a content axis — a
    different observation is a different manifest by design.
    """
    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "source": source,
        "dataset": dataset,
        "logical_key": logical_key,
        "vintage": vintage,
        "state": state,
        "symbols": sorted(symbols),
        "event_date_min": event_date_min,
        "event_date_max": event_date_max,
        "observation_time": observation_time,
        "raw_artifact": {
            "sha256": raw_sha256,
            "object_path": raw_object_path,
            "byte_size": raw_byte_size,
            "row_count": raw_row_count,
            "fetch_mode": fetch_mode,
        },
        "canonical": {
            "schema_version": canonical_schema_version,
            "row_count": canonical_row_count,
            "columns": list(canonical_columns),
        },
        "validation": validation_summary,
        "reference_calendar": calendar_descriptor,
    }


def manifest_digest(manifest: dict[str, Any]) -> str:
    """SHA-256 hex digest of a manifest's canonical serialization."""
    return digest_canonical(manifest)


__all__ = ["MANIFEST_SCHEMA", "build_dataset_manifest", "manifest_digest"]
