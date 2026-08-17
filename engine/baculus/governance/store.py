"""SQLite-backed control-plane store (mirror of the Supabase Postgres plane).

This is the governance/metadata plane: runs, artifacts, observations, validation,
datasets, and the append-only governance/audit ledgers. It stores *metadata and
provenance only* — never large market-data bodies (those live in object storage).

The append-only ledgers are protected by database triggers loaded from
``schema_sqlite.sql``; attempts to UPDATE or DELETE them fail at the DB level.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from baculus.lineage.canonical_json import to_canonical_bytes
from baculus.models.enums import DatasetState, FetchMode, FindingSeverity, GovernanceEventType

_SCHEMA_PATH = Path(__file__).with_name("schema_sqlite.sql")


def _new_id() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> str:
    return to_canonical_bytes(value).decode("utf-8")


def logical_key(source_id: str, dataset: str, event_min: str, event_max: str) -> str:
    """Stable key identifying a logical observed range (for vintaging)."""
    return f"{source_id}|{dataset}|{event_min}|{event_max}"


class GovernanceStore:
    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.commit()

    # -- lifecycle -----------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """Raw connection — exposed for tests probing DB-level enforcement."""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    # -- sources / datasets --------------------------------------------------
    def ensure_source(self, source_id: str, name: str, kind: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO sources(id, name, kind, created_at) VALUES (?,?,?,?)",
            (source_id, name, kind, self._now()),
        )
        self._conn.commit()

    def ensure_source_dataset(self, source_id: str, dataset: str, description: str = "") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO source_datasets(id, source_id, dataset, description, created_at)"
            " VALUES (?,?,?,?,?)",
            (_new_id(), source_id, dataset, description, self._now()),
        )
        self._conn.commit()

    # -- ingestion runs ------------------------------------------------------
    def start_ingestion_run(
        self,
        *,
        source_id: str,
        dataset: str,
        request_parameters: dict[str, str],
        fetch_mode: FetchMode,
        observation_time: datetime,
    ) -> str:
        run_id = _new_id()
        self._conn.execute(
            "INSERT INTO ingestion_runs(id, source_id, dataset, request_parameters, fetch_mode,"
            " observation_time, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                source_id,
                dataset,
                _json(request_parameters),
                fetch_mode.value,
                observation_time.astimezone(UTC).isoformat(),
                "RUNNING",
                self._now(),
            ),
        )
        self._conn.commit()
        return run_id

    def finish_ingestion_run(self, run_id: str, status: str) -> None:
        self._conn.execute("UPDATE ingestion_runs SET status = ? WHERE id = ?", (status, run_id))
        self._conn.commit()

    # -- raw artifacts / observations ---------------------------------------
    def get_artifact_by_sha(self, source_id: str, sha256: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM raw_artifacts WHERE source_id = ? AND sha256 = ?",
            (source_id, sha256),
        )
        return cur.fetchone()

    def artifacts_for_logical_key(self, logical_key_value: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM raw_artifacts WHERE logical_key = ? ORDER BY vintage ASC",
            (logical_key_value,),
        )
        return list(cur.fetchall())

    def next_vintage(self, logical_key_value: str) -> int:
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(vintage), 0) AS m FROM raw_artifacts WHERE logical_key = ?",
            (logical_key_value,),
        )
        row = cur.fetchone()
        return int(row["m"]) + 1

    def insert_raw_artifact(
        self,
        *,
        sha256: str,
        source_id: str,
        dataset: str,
        object_path: str,
        byte_size: int,
        content_type: str,
        file_extension: str,
        event_date_min: str,
        event_date_max: str,
        row_count: int | None,
        schema_version: str,
        logical_key_value: str,
        vintage: int,
        fetch_mode: FetchMode,
    ) -> str:
        self._conn.execute(
            "INSERT INTO raw_artifacts(id, source_id, dataset, sha256, object_path, byte_size,"
            " content_type, file_extension, event_date_min, event_date_max, row_count,"
            " schema_version, logical_key, vintage, fetch_mode, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sha256,
                source_id,
                dataset,
                sha256,
                object_path,
                byte_size,
                content_type,
                file_extension,
                event_date_min,
                event_date_max,
                row_count,
                schema_version,
                logical_key_value,
                vintage,
                fetch_mode.value,
                self._now(),
            ),
        )
        self._conn.commit()
        return sha256

    def insert_raw_observation(
        self,
        *,
        run_id: str,
        artifact_id: str,
        source_id: str,
        dataset: str,
        logical_key_value: str,
        observation_time: datetime,
        is_new_content: bool,
    ) -> str:
        obs_id = _new_id()
        self._conn.execute(
            "INSERT INTO raw_observations(id, run_id, artifact_id, source_id, dataset,"
            " logical_key, observation_time, is_new_content, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                obs_id,
                run_id,
                artifact_id,
                source_id,
                dataset,
                logical_key_value,
                observation_time.astimezone(UTC).isoformat(),
                1 if is_new_content else 0,
                self._now(),
            ),
        )
        self._conn.commit()
        return obs_id

    def observations_for_logical_key(self, logical_key_value: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM raw_observations WHERE logical_key = ? ORDER BY observation_time ASC",
            (logical_key_value,),
        )
        return list(cur.fetchall())

    # -- reference data ------------------------------------------------------
    def ensure_reference_version(
        self, *, kind: str, provider: str, name: str, version: str, vintage: str, descriptor: dict
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO reference_data_versions(id, kind, provider, name, version,"
            " vintage, descriptor, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (_new_id(), kind, provider, name, version, vintage, _json(descriptor), self._now()),
        )
        self._conn.commit()

    # -- dataset builds ------------------------------------------------------
    def create_dataset_build(
        self,
        *,
        source_id: str,
        dataset: str,
        logical_key_value: str,
        vintage: int,
        state: DatasetState = DatasetState.RAW,
    ) -> str:
        build_id = _new_id()
        now = self._now()
        self._conn.execute(
            "INSERT INTO dataset_builds(id, source_id, dataset, logical_key, vintage, state,"
            " manifest_digest, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (build_id, source_id, dataset, logical_key_value, vintage, state.value, None, now, now),
        )
        self._conn.commit()
        return build_id

    def get_dataset_build(self, build_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM dataset_builds WHERE id = ?", (build_id,))
        return cur.fetchone()

    def get_dataset_build_by_key(self, logical_key_value: str, vintage: int) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM dataset_builds WHERE logical_key = ? AND vintage = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (logical_key_value, vintage),
        )
        return cur.fetchone()

    def set_dataset_state(
        self, build_id: str, state: DatasetState, *, manifest_digest: str | None = None
    ) -> None:
        if manifest_digest is None:
            self._conn.execute(
                "UPDATE dataset_builds SET state = ?, updated_at = ? WHERE id = ?",
                (state.value, self._now(), build_id),
            )
        else:
            self._conn.execute(
                "UPDATE dataset_builds SET state = ?, manifest_digest = ?, updated_at = ?"
                " WHERE id = ?",
                (state.value, manifest_digest, self._now(), build_id),
            )
        self._conn.commit()

    def add_dataset_artifact(
        self,
        *,
        dataset_build_id: str,
        kind: str,
        object_path: str,
        sha256: str,
        byte_size: int,
    ) -> str:
        art_id = _new_id()
        self._conn.execute(
            "INSERT INTO dataset_artifacts(id, dataset_build_id, kind, object_path, sha256,"
            " byte_size, created_at) VALUES (?,?,?,?,?,?,?)",
            (art_id, dataset_build_id, kind, object_path, sha256, byte_size, self._now()),
        )
        self._conn.commit()
        return art_id

    # -- validation ----------------------------------------------------------
    def record_validation_run(
        self,
        *,
        dataset_build_id: str | None,
        artifact_id: str | None,
        ruleset_version: str,
        status: str,
        error_count: int,
        warning_count: int,
    ) -> str:
        run_id = _new_id()
        self._conn.execute(
            "INSERT INTO validation_runs(id, dataset_build_id, artifact_id, ruleset_version,"
            " status, error_count, warning_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                dataset_build_id,
                artifact_id,
                ruleset_version,
                status,
                error_count,
                warning_count,
                self._now(),
            ),
        )
        self._conn.commit()
        return run_id

    def record_validation_finding(
        self,
        *,
        validation_run_id: str,
        rule_id: str,
        rule_version: str,
        severity: FindingSeverity,
        source: str | None,
        artifact_id: str | None,
        symbol: str | None,
        event_date: str | None,
        observed_value: str | None,
        reason: str,
    ) -> str:
        fid = _new_id()
        self._conn.execute(
            "INSERT INTO validation_findings(id, validation_run_id, rule_id, rule_version,"
            " severity, source, artifact_id, symbol, event_date, observed_value, reason,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid,
                validation_run_id,
                rule_id,
                rule_version,
                severity.value,
                source,
                artifact_id,
                symbol,
                event_date,
                observed_value,
                reason,
                self._now(),
            ),
        )
        self._conn.commit()
        return fid

    # -- quarantine ----------------------------------------------------------
    def open_quarantine_case(self, *, artifact_id: str | None, dataset: str, reason: str) -> str:
        case_id = _new_id()
        self._conn.execute(
            "INSERT INTO quarantine_cases(id, artifact_id, dataset, reason, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (case_id, artifact_id, dataset, reason, "OPEN", self._now()),
        )
        self._conn.commit()
        return case_id

    def record_quarantine_decision(
        self,
        *,
        case_id: str,
        actor: str,
        reason: str,
        disposition: str,
        evidence_ref: str | None = None,
        triggering_rule_version: str | None = None,
        supersedes_decision_id: str | None = None,
    ) -> str:
        """Append a quarantine decision. Changing a decision means appending a
        superseding decision — the original is never edited (DB-enforced)."""
        dec_id = _new_id()
        self._conn.execute(
            "INSERT INTO quarantine_decisions(id, case_id, actor, decided_at, reason,"
            " evidence_ref, disposition, triggering_rule_version, supersedes_decision_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                dec_id,
                case_id,
                actor,
                self._now(),
                reason,
                evidence_ref,
                disposition,
                triggering_rule_version,
                supersedes_decision_id,
                self._now(),
            ),
        )
        self._conn.commit()
        return dec_id

    # -- append-only ledgers -------------------------------------------------
    def record_governance_event(
        self,
        *,
        event_type: GovernanceEventType,
        subject_type: str,
        subject_id: str | None,
        payload: dict,
        severity: FindingSeverity = FindingSeverity.INFO,
        run_id: str | None = None,
    ) -> str:
        eid = _new_id()
        self._conn.execute(
            "INSERT INTO governance_events(id, event_type, severity, subject_type, subject_id,"
            " run_id, payload, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                eid,
                event_type.value,
                severity.value,
                subject_type,
                subject_id,
                run_id,
                _json(payload),
                self._now(),
            ),
        )
        self._conn.commit()
        return eid

    def record_audit_event(
        self,
        *,
        actor: str,
        action: str,
        subject_type: str,
        subject_id: str | None,
        payload: dict,
    ) -> str:
        eid = _new_id()
        self._conn.execute(
            "INSERT INTO audit_events(id, actor, action, subject_type, subject_id, payload,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (eid, actor, action, subject_type, subject_id, _json(payload), self._now()),
        )
        self._conn.commit()
        return eid

    def governance_events(
        self, *, event_type: GovernanceEventType | None = None
    ) -> list[sqlite3.Row]:
        if event_type is None:
            cur = self._conn.execute(
                "SELECT * FROM governance_events ORDER BY created_at ASC, id ASC"
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM governance_events WHERE event_type = ?"
                " ORDER BY created_at ASC, id ASC",
                (event_type.value,),
            )
        return list(cur.fetchall())


__all__ = ["GovernanceStore", "logical_key"]
