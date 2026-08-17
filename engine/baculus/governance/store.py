"""Postgres-backed control-plane store (the single control plane).

This is the governance/metadata plane: runs, artifacts, observations, validation,
datasets, and the append-only governance/audit ledgers. It stores *metadata and
provenance only* — never large market-data bodies (those live in object storage).

There is exactly ONE control-plane implementation and ONE enforcement model:
Supabase Postgres, defined by ``supabase/migrations`` and reached here via
psycopg. The append-only ledgers and the SEALED guard are enforced by database
triggers created by those migrations; this class never re-implements them.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from baculus.models.enums import DatasetState, FetchMode, FindingSeverity, GovernanceEventType


def _new_id() -> str:
    return str(uuid.uuid4())


def logical_key(source_id: str, dataset: str, event_min: str, event_max: str) -> str:
    """Stable key identifying a logical observed range (for vintaging)."""
    return f"{source_id}|{dataset}|{event_min}|{event_max}"


class GovernanceStore:
    def __init__(
        self,
        dsn: str,
        *,
        clock: Callable[[], datetime] | None = None,
        schema: str = "baculus",
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        # Single enforcement schema; search_path pins it for every statement.
        self._conn.execute(f"SET search_path TO {schema}, public")

    # -- lifecycle -----------------------------------------------------------
    @property
    def connection(self) -> psycopg.Connection[Any]:
        """Raw connection — exposed for tests probing DB-level enforcement."""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    # -- sources / datasets --------------------------------------------------
    def ensure_source(self, source_id: str, name: str, kind: str) -> None:
        self._conn.execute(
            "INSERT INTO sources(id, name, kind) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (source_id, name, kind),
        )

    def ensure_source_dataset(self, source_id: str, dataset: str, description: str = "") -> None:
        self._conn.execute(
            "INSERT INTO source_datasets(source_id, dataset, description) VALUES (%s, %s, %s)"
            " ON CONFLICT (source_id, dataset) DO NOTHING",
            (source_id, dataset, description),
        )

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
            " observation_time, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                source_id,
                dataset,
                Json(request_parameters),
                fetch_mode.value,
                observation_time.astimezone(UTC),
                "RUNNING",
            ),
        )
        return run_id

    def finish_ingestion_run(self, run_id: str, status: str) -> None:
        self._conn.execute("UPDATE ingestion_runs SET status = %s WHERE id = %s", (status, run_id))

    # -- raw artifacts / observations ---------------------------------------
    def get_artifact_by_sha(self, source_id: str, sha256: str) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT * FROM raw_artifacts WHERE source_id = %s AND sha256 = %s",
            (source_id, sha256),
        ).fetchone()

    def artifacts_for_logical_key(self, logical_key_value: str) -> list[dict[str, Any]]:
        return self._conn.execute(
            "SELECT * FROM raw_artifacts WHERE logical_key = %s ORDER BY vintage ASC",
            (logical_key_value,),
        ).fetchall()

    def next_vintage(self, logical_key_value: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(vintage), 0) AS m FROM raw_artifacts WHERE logical_key = %s",
            (logical_key_value,),
        ).fetchone()
        assert row is not None
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
        event_date_min: date,
        event_date_max: date,
        row_count: int | None,
        schema_version: str,
        logical_key_value: str,
        vintage: int,
        fetch_mode: FetchMode,
    ) -> str:
        self._conn.execute(
            "INSERT INTO raw_artifacts(id, source_id, dataset, sha256, object_path, byte_size,"
            " content_type, file_extension, event_date_min, event_date_max, row_count,"
            " schema_version, logical_key, vintage, fetch_mode)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            ),
        )
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
            " logical_key, observation_time, is_new_content) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                obs_id,
                run_id,
                artifact_id,
                source_id,
                dataset,
                logical_key_value,
                observation_time.astimezone(UTC),
                is_new_content,
            ),
        )
        return obs_id

    def observations_for_logical_key(self, logical_key_value: str) -> list[dict[str, Any]]:
        return self._conn.execute(
            "SELECT * FROM raw_observations WHERE logical_key = %s ORDER BY observation_time ASC",
            (logical_key_value,),
        ).fetchall()

    # -- reference data ------------------------------------------------------
    def ensure_reference_version(
        self, *, kind: str, provider: str, name: str, version: str, vintage: str, descriptor: dict
    ) -> None:
        self._conn.execute(
            "INSERT INTO reference_data_versions(kind, provider, name, version, vintage,"
            " descriptor) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (kind, name, version) DO NOTHING",
            (kind, provider, name, version, vintage, Json(descriptor)),
        )

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
        self._conn.execute(
            "INSERT INTO dataset_builds(id, source_id, dataset, logical_key, vintage, state)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (build_id, source_id, dataset, logical_key_value, vintage, state.value),
        )
        return build_id

    def get_dataset_build(self, build_id: str) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT * FROM dataset_builds WHERE id = %s", (build_id,)
        ).fetchone()

    def get_dataset_build_by_key(
        self, logical_key_value: str, vintage: int
    ) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT * FROM dataset_builds WHERE logical_key = %s AND vintage = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (logical_key_value, vintage),
        ).fetchone()

    def set_dataset_state(
        self, build_id: str, state: DatasetState, *, manifest_digest: str | None = None
    ) -> None:
        if manifest_digest is None:
            self._conn.execute(
                "UPDATE dataset_builds SET state = %s, updated_at = now() WHERE id = %s",
                (state.value, build_id),
            )
        else:
            self._conn.execute(
                "UPDATE dataset_builds SET state = %s, manifest_digest = %s, updated_at = now()"
                " WHERE id = %s",
                (state.value, manifest_digest, build_id),
            )

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
            " byte_size) VALUES (%s,%s,%s,%s,%s,%s)",
            (art_id, dataset_build_id, kind, object_path, sha256, byte_size),
        )
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
            " status, error_count, warning_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                dataset_build_id,
                artifact_id,
                ruleset_version,
                status,
                error_count,
                warning_count,
            ),
        )
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
        event_date: date | None,
        observed_value: str | None,
        reason: str,
    ) -> str:
        fid = _new_id()
        self._conn.execute(
            "INSERT INTO validation_findings(id, validation_run_id, rule_id, rule_version,"
            " severity, source, artifact_id, symbol, event_date, observed_value, reason)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            ),
        )
        return fid

    # -- quarantine ----------------------------------------------------------
    def open_quarantine_case(self, *, artifact_id: str | None, dataset: str, reason: str) -> str:
        case_id = _new_id()
        self._conn.execute(
            "INSERT INTO quarantine_cases(id, artifact_id, dataset, reason, status)"
            " VALUES (%s,%s,%s,%s,%s)",
            (case_id, artifact_id, dataset, reason, "OPEN"),
        )
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
            " evidence_ref, disposition, triggering_rule_version, supersedes_decision_id)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            ),
        )
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
            " run_id, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                eid,
                event_type.value,
                severity.value,
                subject_type,
                subject_id,
                run_id,
                Json(payload),
            ),
        )
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
            "INSERT INTO audit_events(id, actor, action, subject_type, subject_id, payload)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (eid, actor, action, subject_type, subject_id, Json(payload)),
        )
        return eid

    def audit_events(self, *, action: str | None = None) -> list[dict[str, Any]]:
        if action is None:
            return self._conn.execute(
                "SELECT * FROM audit_events ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM audit_events WHERE action = %s ORDER BY created_at ASC, id ASC",
            (action,),
        ).fetchall()

    def governance_events(
        self, *, event_type: GovernanceEventType | None = None
    ) -> list[dict[str, Any]]:
        if event_type is None:
            return self._conn.execute(
                "SELECT * FROM governance_events ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM governance_events WHERE event_type = %s ORDER BY created_at ASC, id ASC",
            (event_type.value,),
        ).fetchall()


__all__ = ["GovernanceStore", "logical_key"]
