"""Governance control plane: metadata store, dataset state machine, guards.

The control plane is a single implementation: Supabase Postgres, defined by
``supabase/migrations`` and reached via psycopg. There is one schema and one
enforcement model (append-only triggers, SEALED guard). Dev, CI and tests all
run against a real Postgres (containerized locally, a Postgres service in CI,
Supabase for the hosted POC).
"""

from __future__ import annotations

from baculus.governance.m9_guard import M9EligibilityError, assert_m9_eligible
from baculus.governance.state_machine import (
    ReconciliationEvidence,
    SealGuardError,
    StateTransitionError,
    assert_transition_allowed,
)
from baculus.governance.store import GovernanceStore

__all__ = [
    "GovernanceStore",
    "M9EligibilityError",
    "ReconciliationEvidence",
    "SealGuardError",
    "StateTransitionError",
    "assert_m9_eligible",
    "assert_transition_allowed",
]
