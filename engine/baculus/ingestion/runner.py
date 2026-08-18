"""The ingestion runner.

Orchestrates one ingestion of one request from one source and enforces the
core M0 invariants:

  * raw payload is content-addressed and stored verbatim;
  * an identical refetch does NOT create a duplicate raw artifact (but the run
    is still recorded);
  * changed historical content for the same logical range creates a NEW vintage
    without overwriting the prior artifact, and raises a restatement governance
    event;
  * data is validated deterministically and a dataset build is produced;
  * a single-source (Massive-only) build settles at PROVISIONAL and can never
    be SEALED.

The runner makes no strategy decisions and does not decide source authority.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from baculus import CANONICAL_SCHEMA_VERSION
from baculus.adapters.base import MarketDataSourceAdapter, SourceRequest, VendorArtifact
from baculus.adapters.massive.normalize import MassiveParseError
from baculus.adapters.massive.reasons import MassiveError
from baculus.governance.state_machine import (
    ReconciliationEvidence,
    StateTransitionError,
    assert_transition_allowed,
)
from baculus.governance.store import GovernanceStore, logical_key
from baculus.lineage.anchor import AnchorLedger
from baculus.lineage.canonical_json import sha256_hex, to_canonical_bytes
from baculus.lineage.identity import changed_observations, logical_data_sha256
from baculus.lineage.manifest import build_dataset_manifest, manifest_digest
from baculus.models.bar import CANONICAL_BAR_SCHEMA, CanonicalDailyBar
from baculus.models.enums import DatasetState, FetchMode, FindingSeverity, GovernanceEventType
from baculus.normalization.canonical import build_canonical_frame
from baculus.reference.calendar import TradingCalendar
from baculus.storage.object_store import ObjectStore
from baculus.storage.raw_store import RawArtifactStore
from baculus.validation.findings import ValidationFinding, ValidationReport
from baculus.validation.rules import RULES, RULESET_VERSION, RuleId
from baculus.validation.validator import Validator


@dataclass(frozen=True, slots=True)
class IngestionResult:
    run_id: str
    source: str
    dataset: str
    logical_key: str
    sha256: str
    object_path: str
    byte_size: int
    row_count: int | None
    vintage: int
    is_new_content: bool
    is_identical_refetch: bool
    is_restatement: bool
    dataset_build_id: str | None
    state: DatasetState | None
    manifest_digest: str | None
    quarantined: bool
    validation_summary: dict[str, Any]
    # True when raw bytes changed but the logical market data is unchanged: a new
    # raw observation for provenance, NOT a restatement or a new economic vintage.
    is_raw_only_change: bool = False
    logical_data_sha256: str | None = None
    governance_event_types: list[str] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)


class IngestionRunner:
    def __init__(
        self,
        *,
        store: GovernanceStore,
        object_store: ObjectStore,
        adapter: MarketDataSourceAdapter,
        calendar: TradingCalendar,
        anchor_ledger: AnchorLedger | None = None,
    ) -> None:
        self._store = store
        self._object_store = object_store
        self._raw_store = RawArtifactStore(object_store)
        self._adapter = adapter
        self._calendar = calendar
        self._validator = Validator(calendar=calendar)
        self._anchor = anchor_ledger

    # -- public API ----------------------------------------------------------
    def ingest(self, request: SourceRequest) -> IngestionResult:
        source = self._adapter.source_id
        self._store.ensure_source(source, name=source, kind="market_data_vendor")
        self._store.ensure_source_dataset(source, request.dataset)
        self._register_calendar_reference()

        artifact = self._adapter.fetch(request)
        emin, emax = artifact.event_date_min.isoformat(), artifact.event_date_max.isoformat()
        lkey = logical_key(source, request.dataset, emin, emax)

        run_id = self._store.start_ingestion_run(
            source_id=source,
            dataset=request.dataset,
            request_parameters=self._adapter.describe_request(request),
            fetch_mode=artifact.fetch_mode,
            observation_time=artifact.retrieved_at,
        )
        self._store.record_audit_event(
            actor="ingestion_runner",
            action="ingestion_run_started",
            subject_type="ingestion_run",
            subject_id=run_id,
            payload={"source": source, "dataset": request.dataset, "logical_key": lkey},
        )
        events: list[str] = []

        existing = self._store.get_artifact_by_sha(source, artifact.sha256)
        # Always attempt to store: content-addressed, so this is idempotent.
        put = self._raw_store.put(artifact)

        if existing is not None:
            result = self._handle_identical_refetch(
                request, artifact, lkey, run_id, existing, put.path, events
            )
        else:
            result = self._handle_new_content(request, artifact, lkey, run_id, put.path, events)

        self._store.finish_ingestion_run(
            run_id, "SUCCEEDED" if not result.quarantined else "QUARANTINED"
        )
        return result

    def attempt_seal(
        self, build_id: str, *, reconciliation: ReconciliationEvidence | None = None
    ) -> bool:
        """Attempt PROVISIONAL -> SEALED. Returns True on success.

        On a blocked seal (the M0 single-source case), records a SEAL_BLOCKED
        governance event and returns False. There is no override path.
        """
        build = self._store.get_dataset_build(build_id)
        if build is None:
            raise ValueError(f"unknown dataset build {build_id}")
        current = DatasetState(build["state"])
        try:
            assert_transition_allowed(current, DatasetState.SEALED, reconciliation=reconciliation)
        except StateTransitionError as exc:
            # Covers both the SEALED guard (no independent M4) and any attempt to
            # seal from a state that may not be sealed. Never a silent bypass.
            self._store.record_governance_event(
                event_type=GovernanceEventType.SEAL_BLOCKED,
                severity=FindingSeverity.WARNING,
                subject_type="dataset_build",
                subject_id=build_id,
                payload={"reason": str(exc), "from_state": current.value},
            )
            return False
        self._store.set_dataset_state(build_id, DatasetState.SEALED)
        self._store.record_governance_event(
            event_type=GovernanceEventType.DATASET_STATE_TRANSITION,
            subject_type="dataset_build",
            subject_id=build_id,
            payload={"from": current.value, "to": DatasetState.SEALED.value},
        )
        return True

    # -- internals -----------------------------------------------------------
    def _register_calendar_reference(self) -> None:
        desc = self._calendar.reference_descriptor()
        self._store.ensure_reference_version(
            kind="trading_calendar",
            provider=desc["calendar_provider"],
            name=desc["calendar_name"],
            version=desc["calendar_library_version"],
            vintage=desc["calendar_vintage"],
            descriptor=desc,
        )

    def _handle_identical_refetch(
        self,
        request: SourceRequest,
        artifact: VendorArtifact,
        lkey: str,
        run_id: str,
        existing: dict[str, Any],
        object_path: str,
        events: list[str],
    ) -> IngestionResult:
        vintage = int(existing["vintage"])
        self._store.insert_raw_observation(
            run_id=run_id,
            artifact_id=artifact.sha256,
            source_id=artifact.source,
            dataset=request.dataset,
            logical_key_value=lkey,
            observation_time=artifact.retrieved_at,
            is_new_content=False,
        )
        # An identical refetch is routine daily operation, not a governance
        # event. It is recorded as operational/audit evidence (the observation
        # above is already persisted) so the governance ledger stays high-signal
        # (restatement, quarantine, seal attempts, supersession, ...).
        self._store.record_audit_event(
            actor="ingestion_runner",
            action="identical_refetch",
            subject_type="raw_artifact",
            subject_id=artifact.sha256,
            payload={
                "logical_key": lkey,
                "sha256": artifact.sha256,
                "vintage": vintage,
                "run_id": run_id,
                "note": "identical bytes re-observed; no new market-data content created",
            },
        )

        build = self._store.get_dataset_build_by_key(lkey, vintage)
        state = DatasetState(build["state"]) if build is not None else None
        return IngestionResult(
            run_id=run_id,
            source=artifact.source,
            dataset=request.dataset,
            logical_key=lkey,
            sha256=artifact.sha256,
            object_path=object_path,
            byte_size=artifact.byte_size,
            row_count=artifact.row_count,
            vintage=vintage,
            is_new_content=False,
            is_identical_refetch=True,
            is_restatement=False,
            dataset_build_id=str(build["id"]) if build is not None else None,
            state=state,
            manifest_digest=build["manifest_digest"] if build is not None else None,
            quarantined=state is DatasetState.QUARANTINED,
            validation_summary={"note": "identical refetch; prior validation stands"},
            governance_event_types=events,
        )

    def _handle_new_content(
        self,
        request: SourceRequest,
        artifact: VendorArtifact,
        lkey: str,
        run_id: str,
        object_path: str,
        events: list[str],
    ) -> IngestionResult:
        # Parse first: the ECONOMIC (logical) identity is derived from canonical
        # bars, excluding volatile request/transport metadata. Restatement and
        # economic vintaging key on logical identity, NOT on raw-byte inequality.
        try:
            bars = self._adapter.parse(artifact)
        except (MassiveParseError, MassiveError) as exc:
            # A vendor/identity/integrity failure. Retain the raw evidence for
            # provenance, then quarantine with the exact machine-readable reason.
            unparseable = f"unparseable:{artifact.sha256}"
            vintage, _, _ = self._store.resolve_economic_vintage(lkey, unparseable)
            self._persist_raw(artifact, request, lkey, object_path, run_id, vintage, unparseable)
            self._emit_raw_artifact_stored(
                artifact, lkey, object_path, run_id, vintage, unparseable, events
            )
            build_id = self._store.create_dataset_build(
                source_id=artifact.source,
                dataset=request.dataset,
                logical_key_value=lkey,
                vintage=vintage,
                state=DatasetState.RAW,
            )
            return self._quarantine_build(
                request,
                artifact,
                lkey,
                run_id,
                build_id,
                vintage,
                object_path,
                events,
                False,
                parse_error=exc if isinstance(exc, MassiveParseError) else None,
                massive_error=exc if isinstance(exc, MassiveError) else None,
            )

        logical_sha = logical_data_sha256(bars)
        prior_artifacts = self._store.artifacts_for_logical_key(lkey)
        vintage, is_new_economic, match_row = self._store.resolve_economic_vintage(
            lkey, logical_sha
        )
        self._persist_raw(artifact, request, lkey, object_path, run_id, vintage, logical_sha)

        if not is_new_economic:
            # Raw bytes changed but the logical market data is identical to an
            # existing vintage: a new raw observation for provenance, NOT a
            # restatement and NOT a new economic vintage. Provenance/audit only —
            # no RAW_ARTIFACT_STORED governance event.
            return self._handle_raw_only_change(
                request,
                artifact,
                lkey,
                run_id,
                object_path,
                events,
                vintage,
                match_row,
                logical_sha,
            )

        self._emit_raw_artifact_stored(
            artifact, lkey, object_path, run_id, vintage, logical_sha, events
        )
        is_restatement = len(prior_artifacts) > 0
        if is_restatement:
            self._store.record_governance_event(
                event_type=GovernanceEventType.RESTATEMENT_DETECTED,
                severity=FindingSeverity.WARNING,
                subject_type="raw_artifact",
                subject_id=artifact.sha256,
                run_id=run_id,
                payload={
                    "logical_key": lkey,
                    "new_sha256": artifact.sha256,
                    "new_logical_data_sha256": logical_sha,
                    "new_vintage": vintage,
                    "changed_observations": self._diff_prior(prior_artifacts, bars),
                    "note": "vendor redelivered CHANGED market data; prior vintage preserved",
                },
            )
            self._store.record_governance_event(
                event_type=GovernanceEventType.NEW_VINTAGE_CREATED,
                subject_type="raw_artifact",
                subject_id=artifact.sha256,
                run_id=run_id,
                payload={"logical_key": lkey, "vintage": vintage},
            )
            events.append(GovernanceEventType.RESTATEMENT_DETECTED.value)
            events.append(GovernanceEventType.NEW_VINTAGE_CREATED.value)

        # Create the dataset build up front so validation/quarantine can attach.
        build_id = self._store.create_dataset_build(
            source_id=artifact.source,
            dataset=request.dataset,
            logical_key_value=lkey,
            vintage=vintage,
            state=DatasetState.RAW,
        )
        df = build_canonical_frame(bars)
        report = self._validator.validate(
            df,
            source=artifact.source,
            artifact_id=artifact.sha256,
            symbols=request.normalized_symbols(),
            start=request.start,
            end=request.end,
        )
        status = "PASSED" if report.passed else "FAILED"
        vr_id = self._store.record_validation_run(
            dataset_build_id=build_id,
            artifact_id=artifact.sha256,
            ruleset_version=RULESET_VERSION,
            status=status,
            error_count=report.error_count,
            warning_count=report.warning_count,
        )
        for f in report.findings:
            self._store.record_validation_finding(
                validation_run_id=vr_id,
                rule_id=f.rule_id.value,
                rule_version=f.rule_version,
                severity=f.severity,
                source=f.source,
                artifact_id=f.artifact_id,
                symbol=f.symbol,
                event_date=f.event_date,
                observed_value=f.observed_value,
                reason=f.reason,
            )
        self._store.record_governance_event(
            event_type=GovernanceEventType.VALIDATION_COMPLETED,
            severity=FindingSeverity.INFO if report.passed else FindingSeverity.ERROR,
            subject_type="dataset_build",
            subject_id=build_id,
            run_id=run_id,
            payload=report.summary(),
        )
        events.append(GovernanceEventType.VALIDATION_COMPLETED.value)

        if not report.passed:
            return self._quarantine_build(
                request,
                artifact,
                lkey,
                run_id,
                build_id,
                vintage,
                object_path,
                events,
                is_restatement,
                report=report,
                df=df,
            )

        # RAW -> VALIDATED -> PROVISIONAL (single-source ceiling).
        self._transition(build_id, DatasetState.RAW, DatasetState.VALIDATED)
        self._transition(build_id, DatasetState.VALIDATED, DatasetState.PROVISIONAL)
        state = DatasetState.PROVISIONAL

        digest = self._finalize_manifest(
            request, artifact, lkey, build_id, vintage, state, df, report.summary()
        )
        return IngestionResult(
            run_id=run_id,
            source=artifact.source,
            dataset=request.dataset,
            logical_key=lkey,
            sha256=artifact.sha256,
            object_path=object_path,
            byte_size=artifact.byte_size,
            row_count=artifact.row_count,
            vintage=vintage,
            is_new_content=True,
            is_identical_refetch=False,
            is_restatement=is_restatement,
            dataset_build_id=build_id,
            state=state,
            manifest_digest=digest,
            quarantined=False,
            validation_summary=report.summary(),
            logical_data_sha256=logical_sha,
            governance_event_types=events,
            findings=report.findings,
        )

    def _persist_raw(
        self,
        artifact: VendorArtifact,
        request: SourceRequest,
        lkey: str,
        object_path: str,
        run_id: str,
        vintage: int,
        logical_sha: str,
    ) -> None:
        """Persist the raw artifact + observation (exact raw provenance) only.

        Emits no governance event: the caller decides whether the storage is a
        governance-worthy new-economic artifact or a provenance-only raw change.
        """
        self._store.insert_raw_artifact(
            sha256=artifact.sha256,
            source_id=artifact.source,
            dataset=request.dataset,
            object_path=object_path,
            byte_size=artifact.byte_size,
            content_type=artifact.content_type,
            file_extension=artifact.file_extension,
            event_date_min=artifact.event_date_min,
            event_date_max=artifact.event_date_max,
            row_count=artifact.row_count,
            schema_version=CANONICAL_SCHEMA_VERSION,
            logical_key_value=lkey,
            logical_data_sha256=logical_sha,
            vintage=vintage,
            fetch_mode=artifact.fetch_mode,
        )
        self._store.insert_raw_observation(
            run_id=run_id,
            artifact_id=artifact.sha256,
            source_id=artifact.source,
            dataset=request.dataset,
            logical_key_value=lkey,
            observation_time=artifact.retrieved_at,
            is_new_content=True,
        )

    def _emit_raw_artifact_stored(
        self,
        artifact: VendorArtifact,
        lkey: str,
        object_path: str,
        run_id: str,
        vintage: int,
        logical_sha: str,
        events: list[str],
    ) -> None:
        self._store.record_governance_event(
            event_type=GovernanceEventType.RAW_ARTIFACT_STORED,
            subject_type="raw_artifact",
            subject_id=artifact.sha256,
            run_id=run_id,
            payload={
                "logical_key": lkey,
                "sha256": artifact.sha256,
                "object_path": object_path,
                "vintage": vintage,
                "logical_data_sha256": logical_sha,
                "fetch_mode": artifact.fetch_mode.value,
            },
        )
        events.append(GovernanceEventType.RAW_ARTIFACT_STORED.value)

    def _handle_raw_only_change(
        self,
        request: SourceRequest,
        artifact: VendorArtifact,
        lkey: str,
        run_id: str,
        object_path: str,
        events: list[str],
        vintage: int,
        match_row: dict[str, Any] | None,
        logical_sha: str,
    ) -> IngestionResult:
        """A new raw observation whose logical market data equals an existing
        vintage: recorded as operational/audit provenance, never governance."""
        self._store.record_audit_event(
            actor="ingestion_runner",
            action="raw_metadata_changed_same_logical",
            subject_type="raw_artifact",
            subject_id=artifact.sha256,
            payload={
                "logical_key": lkey,
                "new_sha256": artifact.sha256,
                "prior_sha256": match_row["sha256"] if match_row else None,
                "logical_data_sha256": logical_sha,
                "vintage": vintage,
                "run_id": run_id,
                "note": (
                    "raw bytes changed (volatile metadata) but market data identical; "
                    "new raw observation, not a restatement"
                ),
            },
        )
        build = self._store.get_dataset_build_by_key(lkey, vintage)
        state = DatasetState(build["state"]) if build is not None else None
        return IngestionResult(
            run_id=run_id,
            source=artifact.source,
            dataset=request.dataset,
            logical_key=lkey,
            sha256=artifact.sha256,
            object_path=object_path,
            byte_size=artifact.byte_size,
            row_count=artifact.row_count,
            vintage=vintage,
            is_new_content=True,
            is_identical_refetch=False,
            is_restatement=False,
            dataset_build_id=str(build["id"]) if build is not None else None,
            state=state,
            manifest_digest=build["manifest_digest"] if build is not None else None,
            quarantined=state is DatasetState.QUARANTINED,
            validation_summary={"note": "raw-only change; logical data unchanged"},
            is_raw_only_change=True,
            logical_data_sha256=logical_sha,
            governance_event_types=events,
        )

    def _diff_prior(
        self, prior_artifacts: list[dict[str, Any]], current_bars: list[CanonicalDailyBar]
    ) -> list[str]:
        """Identify which canonical observations changed vs the latest prior
        economic vintage (best-effort; empty if the prior cannot be re-read)."""
        if not prior_artifacts:
            return []
        prior_row = max(prior_artifacts, key=lambda r: int(r["vintage"]))
        try:
            prior_bytes = self._object_store.get(prior_row["object_path"])
            prior_artifact = VendorArtifact(
                source=prior_row["source_id"],
                dataset=prior_row["dataset"],
                payload=prior_bytes,
                content_type=prior_row["content_type"],
                file_extension=prior_row["file_extension"],
                fetch_mode=FetchMode(prior_row["fetch_mode"]),
                retrieved_at=datetime(1970, 1, 1, tzinfo=UTC),
                event_date_min=prior_row["event_date_min"],
                event_date_max=prior_row["event_date_max"],
                row_count=prior_row["row_count"],
            )
            prior_bars = self._adapter.parse(prior_artifact)
        except (MassiveParseError, MassiveError, OSError, RuntimeError, ValueError, KeyError):
            return []
        return changed_observations(prior_bars, current_bars)

    def _quarantine_build(
        self,
        request: SourceRequest,
        artifact: VendorArtifact,
        lkey: str,
        run_id: str,
        build_id: str,
        vintage: int,
        object_path: str,
        events: list[str],
        is_restatement: bool,
        *,
        report: ValidationReport | None = None,
        df: pl.DataFrame | None = None,
        parse_error: MassiveParseError | None = None,
        massive_error: MassiveError | None = None,
    ) -> IngestionResult:
        findings: list[ValidationFinding] = list(report.findings) if report is not None else []
        if massive_error is not None:
            # A vendor/identity/integrity failure with an exact reason code.
            vr_id = self._store.record_validation_run(
                dataset_build_id=build_id,
                artifact_id=artifact.sha256,
                ruleset_version=RULESET_VERSION,
                status="FAILED",
                error_count=1,
                warning_count=0,
            )
            self._store.record_validation_finding(
                validation_run_id=vr_id,
                rule_id=massive_error.reason.value,
                rule_version="1.0.0",
                severity=FindingSeverity.ERROR,
                source=artifact.source,
                artifact_id=artifact.sha256,
                symbol=None,
                event_date=None,
                observed_value=(
                    f"http_status={massive_error.http_status}"
                    if massive_error.http_status
                    else None
                ),
                reason=str(massive_error),
            )
        if parse_error is not None:
            rule = RULES[RuleId.MALFORMED_RECORD]
            vr_id = self._store.record_validation_run(
                dataset_build_id=build_id,
                artifact_id=artifact.sha256,
                ruleset_version=RULESET_VERSION,
                status="FAILED",
                error_count=len(parse_error.errors),
                warning_count=0,
            )
            for err in parse_error.errors:
                self._store.record_validation_finding(
                    validation_run_id=vr_id,
                    rule_id=rule.id.value,
                    rule_version=rule.version,
                    severity=rule.severity,
                    source=artifact.source,
                    artifact_id=artifact.sha256,
                    symbol=None,
                    event_date=None,
                    observed_value=f"row={err.index}",
                    reason=err.reason,
                )
                findings.append(
                    ValidationFinding(
                        rule_id=rule.id,
                        rule_version=rule.version,
                        severity=rule.severity,
                        reason=err.reason,
                        source=artifact.source,
                        artifact_id=artifact.sha256,
                        observed_value=f"row={err.index}",
                    )
                )

        self._transition(build_id, DatasetState.RAW, DatasetState.QUARANTINED)
        if massive_error is not None:
            case_reason = massive_error.reason.value
        elif parse_error is not None:
            case_reason = "malformed payload"
        else:
            case_reason = "validation errors"
        case_id = self._store.open_quarantine_case(
            artifact_id=artifact.sha256,
            dataset=request.dataset,
            reason=case_reason,
        )
        self._store.record_governance_event(
            event_type=GovernanceEventType.QUARANTINE_OPENED,
            severity=FindingSeverity.ERROR,
            subject_type="dataset_build",
            subject_id=build_id,
            run_id=run_id,
            payload={"case_id": case_id, "artifact": artifact.sha256},
        )
        events.append(GovernanceEventType.QUARANTINE_OPENED.value)
        if report is not None:
            summary: dict[str, Any] = report.summary()
        elif massive_error is not None:
            summary = {"quarantine_reason": massive_error.reason.value}
        else:
            summary = {"malformed": True}
        return IngestionResult(
            run_id=run_id,
            source=artifact.source,
            dataset=request.dataset,
            logical_key=lkey,
            sha256=artifact.sha256,
            object_path=object_path,
            byte_size=artifact.byte_size,
            row_count=artifact.row_count,
            vintage=vintage,
            is_new_content=True,
            is_identical_refetch=False,
            is_restatement=is_restatement,
            dataset_build_id=build_id,
            state=DatasetState.QUARANTINED,
            manifest_digest=None,
            quarantined=True,
            validation_summary=summary,
            governance_event_types=events,
            findings=findings,
        )

    def _transition(self, build_id: str, current: DatasetState, target: DatasetState) -> None:
        assert_transition_allowed(current, target)
        self._store.set_dataset_state(build_id, target)
        self._store.record_governance_event(
            event_type=GovernanceEventType.DATASET_STATE_TRANSITION,
            subject_type="dataset_build",
            subject_id=build_id,
            payload={"from": current.value, "to": target.value},
        )

    def _finalize_manifest(
        self,
        request: SourceRequest,
        artifact: VendorArtifact,
        lkey: str,
        build_id: str,
        vintage: int,
        state: DatasetState,
        df: pl.DataFrame,
        validation_summary: dict[str, Any],
    ) -> str:
        # Persist the canonical dataset as parquet in object storage.
        buf = io.BytesIO()
        df.write_parquet(buf)
        canonical_bytes = buf.getvalue()
        canonical_path = (
            f"canonical/{artifact.source}/{request.dataset}/"
            f"vintage={vintage}/{artifact.sha256}.parquet"
        )
        self._object_store.put(canonical_path, canonical_bytes)
        self._store.add_dataset_artifact(
            dataset_build_id=build_id,
            kind="canonical_parquet",
            object_path=canonical_path,
            sha256=sha256_hex(canonical_bytes),
            byte_size=len(canonical_bytes),
        )

        manifest = build_dataset_manifest(
            source=artifact.source,
            dataset=request.dataset,
            logical_key=lkey,
            vintage=vintage,
            state=state.value,
            symbols=request.normalized_symbols(),
            event_date_min=artifact.event_date_min,
            event_date_max=artifact.event_date_max,
            observation_time=artifact.retrieved_at,
            raw_sha256=artifact.sha256,
            raw_object_path=self._raw_store.build_path(artifact),
            raw_byte_size=artifact.byte_size,
            raw_row_count=artifact.row_count,
            fetch_mode=artifact.fetch_mode.value,
            canonical_schema_version=CANONICAL_SCHEMA_VERSION,
            canonical_row_count=df.height,
            canonical_columns=tuple(CANONICAL_BAR_SCHEMA.keys()),
            validation_summary=validation_summary,
            calendar_descriptor=self._calendar.reference_descriptor(),
        )
        digest = manifest_digest(manifest)

        manifest_bytes = to_canonical_bytes(manifest)
        manifest_path = (
            f"manifests/{artifact.source}/{request.dataset}/vintage={vintage}/{digest}.json"
        )
        self._object_store.put(manifest_path, manifest_bytes)
        self._store.add_dataset_artifact(
            dataset_build_id=build_id,
            kind="manifest",
            object_path=manifest_path,
            sha256=digest,
            byte_size=len(manifest_bytes),
        )
        self._store.set_dataset_state(build_id, state, manifest_digest=digest)

        if self._anchor is not None:
            self._anchor.append(
                subject_type="dataset_manifest",
                subject_id=build_id,
                digest=digest,
                at=artifact.retrieved_at,
            )
        return digest


__all__ = ["IngestionResult", "IngestionRunner"]
