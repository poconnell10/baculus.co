"""Enumerations shared across the Baculus engine.

These are deliberately explicit string enums so they serialize deterministically
into manifests, JSON metadata, and the Postgres control plane.
"""

from __future__ import annotations

from enum import StrEnum


class FetchMode(StrEnum):
    """How a vendor artifact was obtained.

    This is recorded on every run/artifact so fixture-based proof can never be
    mistaken for live-vendor proof.
    """

    LIVE = "live"
    FIXTURE = "fixture"


class DatasetState(StrEnum):
    """Explicit lifecycle states for a built dataset.

    The ordering here is documentation only; permitted transitions are defined
    in :mod:`baculus.governance.state_machine`.
    """

    RAW = "RAW"
    VALIDATED = "VALIDATED"
    PROVISIONAL = "PROVISIONAL"
    QUARANTINED = "QUARANTINED"
    SEALED = "SEALED"
    SUPERSEDED = "SUPERSEDED"


class FindingSeverity(StrEnum):
    """Severity of a deterministic validation finding.

    ERROR findings block promotion of a dataset out of RAW. WARNING findings are
    recorded but non-blocking. INFO is purely contextual.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class GovernanceEventType(StrEnum):
    """Categories of governance events written to the append-only ledger."""

    INGESTION_STARTED = "INGESTION_STARTED"
    RAW_ARTIFACT_STORED = "RAW_ARTIFACT_STORED"
    IDENTICAL_REFETCH = "IDENTICAL_REFETCH"
    NEW_VINTAGE_CREATED = "NEW_VINTAGE_CREATED"
    RESTATEMENT_DETECTED = "RESTATEMENT_DETECTED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    DATASET_STATE_TRANSITION = "DATASET_STATE_TRANSITION"
    SEAL_BLOCKED = "SEAL_BLOCKED"
    QUARANTINE_OPENED = "QUARANTINE_OPENED"


__all__ = [
    "DatasetState",
    "FetchMode",
    "FindingSeverity",
    "GovernanceEventType",
]
