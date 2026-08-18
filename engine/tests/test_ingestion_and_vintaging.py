"""Idempotency and vintaging — the heart of the M0 acceptance gate.

Covers required tests:
  1. identical ingestion does not create duplicate raw market-data artifacts
  2. changed historical content creates a new vintage
  3. original vintage remains retrievable
  4. changed historical content raises a governance event
"""

from __future__ import annotations

from baculus.models.enums import DatasetState, GovernanceEventType


def _raw_artifact_count(store) -> int:
    return store.connection.execute("SELECT COUNT(*) AS c FROM raw_artifacts").fetchone()["c"]


def test_first_ingestion_produces_provisional_single_source(make_runner, proof_request, store):
    runner = make_runner()
    result = runner.ingest(proof_request)

    assert result.is_new_content is True
    assert result.is_identical_refetch is False
    assert result.vintage == 1
    assert result.state is DatasetState.PROVISIONAL  # single-source ceiling
    assert result.row_count == 183  # 61 XNYS sessions in Q1 2024 * 3 symbols
    assert result.manifest_digest is not None
    assert _raw_artifact_count(store) == 1


def test_identical_ingestion_creates_no_duplicate_artifact(make_runner, proof_request, store):
    """Required test #1."""
    runner = make_runner()
    first = runner.ingest(proof_request)
    second = runner.ingest(proof_request)

    # Same content hash, no new raw artifact row, object not rewritten.
    assert second.sha256 == first.sha256
    assert second.is_identical_refetch is True
    assert second.is_new_content is False
    assert second.vintage == 1
    assert _raw_artifact_count(store) == 1

    # But the run IS still recorded in the audit trail (two runs, two observations).
    runs = store.connection.execute("SELECT COUNT(*) AS c FROM ingestion_runs").fetchone()["c"]
    obs = store.connection.execute("SELECT COUNT(*) AS c FROM raw_observations").fetchone()["c"]
    assert runs == 2
    assert obs == 2


def test_identical_refetch_is_operational_not_governance(make_runner, proof_request, store):
    """Identical refetch is audit/operational evidence, not a governance event."""
    runner = make_runner()
    runner.ingest(proof_request)
    governance_before = len(store.governance_events())
    second = runner.ingest(proof_request)

    # No governance event was raised by the identical refetch...
    assert second.governance_event_types == []
    assert len(store.governance_events()) == governance_before
    # ...but it IS recorded as an operational audit event.
    refetch_audits = store.audit_events(action="identical_refetch")
    assert len(refetch_audits) == 1
    assert refetch_audits[0]["subject_id"] == second.sha256


def test_changed_history_creates_new_vintage(make_runner, proof_request, store):
    """Required test #2."""
    clean = make_runner()
    v1 = clean.ingest(proof_request)

    # A vendor restatement: same logical range, one changed (valid) value.
    restated = make_runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}})
    v2 = restated.ingest(proof_request)

    assert v2.sha256 != v1.sha256
    assert v2.is_new_content is True
    assert v2.is_restatement is True
    assert v2.vintage == 2
    assert _raw_artifact_count(store) == 2

    vintages = [row["vintage"] for row in store.artifacts_for_logical_key(v1.logical_key)]
    assert vintages == [1, 2]


def test_original_vintage_remains_retrievable(make_runner, proof_request, object_store):
    """Required test #3."""
    clean = make_runner()
    v1 = clean.ingest(proof_request)
    original_bytes = object_store.get(v1.object_path)

    restated = make_runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}})
    v2 = restated.ingest(proof_request)

    # The original object is untouched and still returns the original bytes.
    assert object_store.exists(v1.object_path)
    assert object_store.get(v1.object_path) == original_bytes
    assert v2.object_path != v1.object_path
    # And the second vintage's bytes differ.
    assert object_store.get(v2.object_path) != original_bytes


def test_restatement_raises_governance_event(make_runner, proof_request, store):
    """Required test #4."""
    make_runner().ingest(proof_request)
    restated = make_runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}})
    result = restated.ingest(proof_request)

    assert GovernanceEventType.RESTATEMENT_DETECTED.value in result.governance_event_types
    events = store.governance_events(event_type=GovernanceEventType.RESTATEMENT_DETECTED)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert "new_sha256" in payload
    assert "new_logical_data_sha256" in payload  # keyed on logical, not raw, identity
    # The event identifies exactly which canonical observation changed.
    assert payload["changed_observations"] == ["SPY:2024-01-02"]


def test_changed_request_id_same_bars_is_not_a_restatement(make_runner, proof_request, store):
    """Raw-only change (volatile metadata) must NOT be a restatement/new vintage.

    Same market data, different vendor request id => different raw bytes but
    identical logical data. This is the M0 provenance correction: restatement is
    keyed on logical identity, not raw-byte inequality.
    """
    v1 = make_runner(nonce="request-A").ingest(proof_request)
    # Same bars, DIFFERENT request id -> different raw SHA, identical logical data.
    v2 = make_runner(nonce="request-B").ingest(proof_request)

    assert v2.sha256 != v1.sha256  # raw bytes differ (volatile request id)
    assert v2.logical_data_sha256 == v1.logical_data_sha256  # market data identical
    assert v2.is_raw_only_change is True
    assert v2.is_restatement is False
    assert v2.vintage == v1.vintage == 1  # no new economic vintage
    assert v2.dataset_build_id == v1.dataset_build_id  # existing PROVISIONAL build stands

    # A second raw artifact is retained for exact provenance...
    assert _raw_artifact_count(store) == 2
    # ...recorded as an operational audit event, NOT a governance restatement.
    assert GovernanceEventType.RESTATEMENT_DETECTED.value not in v2.governance_event_types
    assert store.governance_events(event_type=GovernanceEventType.RESTATEMENT_DETECTED) == []
    assert len(store.audit_events(action="raw_metadata_changed_same_logical")) == 1


def test_changed_bar_with_different_request_id_is_still_a_restatement(
    make_runner, proof_request, store
):
    """Changed historical bar (+ different request id) is a true restatement."""
    make_runner(nonce="request-A").ingest(proof_request)
    v2 = make_runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}}, nonce="request-B").ingest(
        proof_request
    )
    assert v2.is_restatement is True
    assert v2.is_raw_only_change is False
    assert v2.vintage == 2
    assert len(store.governance_events(event_type=GovernanceEventType.RESTATEMENT_DETECTED)) == 1
