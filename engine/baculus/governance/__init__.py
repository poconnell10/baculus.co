"""Governance control plane: metadata store, dataset state machine, guards.

Postgres (Supabase) is the production control plane; this package also ships a
SQLite mirror with equivalent schema and append-only triggers so the governance
invariants are executable and testable without a live database. The Postgres
DDL under ``supabase/migrations`` mirrors ``schema_sqlite.sql``.
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
