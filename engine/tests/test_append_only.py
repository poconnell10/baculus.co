"""Append-only governance controls enforced by database triggers.

Covers required tests:
 15. append-only governance UPDATE fails
 16. append-only governance DELETE fails
 17. audit event UPDATE fails
 18. audit event DELETE fails

The triggers (RAISE(ABORT)) surface in Python as sqlite3.IntegrityError.
"""

from __future__ import annotations

import sqlite3

import pytest

from baculus.models.enums import GovernanceEventType


def _insert_governance_event(store) -> str:
    return store.record_governance_event(
        event_type=GovernanceEventType.RAW_ARTIFACT_STORED,
        subject_type="raw_artifact",
        subject_id="sha",
        payload={"k": "v"},
    )


def _insert_audit_event(store) -> str:
    return store.record_audit_event(
        actor="tester",
        action="did_thing",
        subject_type="thing",
        subject_id="1",
        payload={"k": "v"},
    )


def test_governance_event_update_fails(store):
    """Required test #15."""
    eid = _insert_governance_event(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "UPDATE governance_events SET severity = 'ERROR' WHERE id = ?", (eid,)
        )


def test_governance_event_delete_fails(store):
    """Required test #16."""
    eid = _insert_governance_event(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM governance_events WHERE id = ?", (eid,))
    # Row is still present.
    row = store.connection.execute(
        "SELECT COUNT(*) AS c FROM governance_events WHERE id = ?", (eid,)
    ).fetchone()
    assert row["c"] == 1


def test_audit_event_update_fails(store):
    """Required test #17."""
    eid = _insert_audit_event(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "UPDATE audit_events SET actor = 'someone_else' WHERE id = ?", (eid,)
        )


def test_audit_event_delete_fails(store):
    """Required test #18."""
    eid = _insert_audit_event(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM audit_events WHERE id = ?", (eid,))


def test_quarantine_decision_is_append_only(store):
    case_id = store.open_quarantine_case(artifact_id=None, dataset="stocks/daily", reason="test")
    dec_id = store.record_quarantine_decision(
        case_id=case_id, actor="tester", reason="hold", disposition="QUARANTINE"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "UPDATE quarantine_decisions SET disposition = 'RELEASE' WHERE id = ?", (dec_id,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM quarantine_decisions WHERE id = ?", (dec_id,))

    # A changed decision is a NEW superseding row, not an edit.
    superseding = store.record_quarantine_decision(
        case_id=case_id,
        actor="reviewer",
        reason="released after review",
        disposition="RELEASE",
        supersedes_decision_id=dec_id,
    )
    rows = store.connection.execute(
        "SELECT COUNT(*) AS c FROM quarantine_decisions WHERE case_id = ?", (case_id,)
    ).fetchone()
    assert rows["c"] == 2
    assert superseding != dec_id
