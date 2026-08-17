"""Dataset state machine, the SEALED guard, and M9 protection.

Covers required test:
 14. PROVISIONAL -> SEALED fails without M4

Plus M9 eligibility protection (section 9) and the runner-level seal path.
"""

from __future__ import annotations

import pytest

from baculus.governance.m9_guard import M9EligibilityError, assert_m9_eligible, is_m9_eligible
from baculus.governance.state_machine import (
    ReconciliationEvidence,
    SealGuardError,
    StateTransitionError,
    assert_transition_allowed,
)
from baculus.models.enums import DatasetState, GovernanceEventType


def test_provisional_to_sealed_fails_without_m4():
    """Required test #14."""
    with pytest.raises(SealGuardError):
        assert_transition_allowed(
            DatasetState.PROVISIONAL, DatasetState.SEALED, reconciliation=None
        )


def test_provisional_to_sealed_fails_with_single_source_reconciliation():
    massive_only = ReconciliationEvidence(
        dataset_source="massive", sources=frozenset({"massive"}), passed=True
    )
    with pytest.raises(SealGuardError):
        assert_transition_allowed(
            DatasetState.PROVISIONAL, DatasetState.SEALED, reconciliation=massive_only
        )


def test_provisional_to_sealed_fails_when_reconciliation_failed():
    failed = ReconciliationEvidence(
        dataset_source="massive", sources=frozenset({"massive", "sourceB"}), passed=False
    )
    with pytest.raises(SealGuardError):
        assert_transition_allowed(
            DatasetState.PROVISIONAL, DatasetState.SEALED, reconciliation=failed
        )


def test_provisional_to_sealed_succeeds_with_independent_passed_m4():
    independent = ReconciliationEvidence(
        dataset_source="massive", sources=frozenset({"massive", "sourceB"}), passed=True
    )
    # Should not raise.
    assert_transition_allowed(
        DatasetState.PROVISIONAL, DatasetState.SEALED, reconciliation=independent
    )


def test_invalid_edges_are_rejected():
    with pytest.raises(StateTransitionError):
        assert_transition_allowed(DatasetState.RAW, DatasetState.SEALED)
    with pytest.raises(StateTransitionError):
        assert_transition_allowed(DatasetState.SUPERSEDED, DatasetState.RAW)


def test_runner_seal_blocked_records_event(make_runner, proof_request, store):
    runner = make_runner()
    result = runner.ingest(proof_request)
    assert result.state is DatasetState.PROVISIONAL

    assert runner.attempt_seal(result.dataset_build_id) is False
    massive_only = ReconciliationEvidence(
        dataset_source="massive", sources=frozenset({"massive"}), passed=True
    )
    assert runner.attempt_seal(result.dataset_build_id, reconciliation=massive_only) is False

    blocked = store.governance_events(event_type=GovernanceEventType.SEAL_BLOCKED)
    assert len(blocked) == 2

    # State is unchanged — still PROVISIONAL, never SEALED.
    build = store.get_dataset_build(result.dataset_build_id)
    assert build["state"] == DatasetState.PROVISIONAL.value


# --- M9 protection (section 9) ---------------------------------------------
def test_only_sealed_is_m9_eligible():
    for state in DatasetState:
        assert is_m9_eligible(state) == (state is DatasetState.SEALED)


def test_provisional_data_is_not_m9_eligible():
    with pytest.raises(M9EligibilityError):
        assert_m9_eligible(DatasetState.PROVISIONAL, dataset_id="build-1")
    # Sealed passes.
    assert_m9_eligible(DatasetState.SEALED)
