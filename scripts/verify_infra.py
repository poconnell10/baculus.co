#!/usr/bin/env python3
"""Real-stack M0 infrastructure proof (Proof 4): fixture vendor, REAL storage +
REAL Postgres control plane.

This is the driver for M0 closure Proof 4. It runs the existing proof cohort
(SPY/XLK/XLE, 2024-01-01..2024-03-31) with:

  * vendor side = deterministic Massive-format FIXTURES (no live Massive call),
  * raw storage = the env-selected object store (set BACULUS_OBJECT_STORE=supabase
    for real Supabase Storage),
  * control/provenance = the real Postgres given by --admin-url,
  * validation = the actual Baculus validator,

and proves the full flow lands at PROVISIONAL, that an identical rerun creates no
duplicate raw content and no governance restatement, and that changed historical
bytes create a new vintage (original preserved) plus a restatement governance
event.

It NEVER calls live Massive and NEVER claims live-vendor evidence: the emitted
mode is "INFRA" and the vendor side is labelled "fixture".

Usage (on the Mac, with .env loaded and Supabase migrated via `supabase db push`):
    set -a && source .env && set +a
    export BACULUS_OBJECT_STORE=supabase
    python scripts/verify_infra.py \
        --admin-url "postgresql://<supabase session pooler DSN>" \
        --out docs/evidence/m0_infra_proof.json

Re-running against the same DB requires --reset (truncates baculus.* first) or a
fresh --run-tag, because raw_artifacts is unique on (logical_key, vintage).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

import psycopg

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.governance.migrations import apply_migrations
from baculus.governance.state_machine import ReconciliationEvidence
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.models.enums import GovernanceEventType
from baculus.reference.calendar import TradingCalendar
from baculus.storage import object_store_from_env

FIXED_OBSERVATION = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)
_BACULUS_TABLES = [
    "governance_events",
    "audit_events",
    "quarantine_decisions",
    "quarantine_cases",
    "validation_findings",
    "validation_runs",
    "dataset_artifacts",
    "dataset_builds",
    "reconciliations",
    "raw_observations",
    "raw_artifacts",
    "reference_data_versions",
    "ingestion_runs",
    "source_datasets",
    "sources",
]


def _cohort(run_tag: str) -> SourceRequest:
    dataset = "stocks/daily" if not run_tag else f"stocks/daily/{run_tag}"
    return SourceRequest(
        dataset=dataset,
        symbols=("SPY", "XLK", "XLE"),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
    )


def _reset(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET search_path TO baculus, public")
        present = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'baculus'"
            ).fetchall()
        }
        targets = [t for t in _BACULUS_TABLES if t in present]
        if targets:
            cols = ", ".join(f'baculus."{t}"' for t in targets)
            conn.execute(f"TRUNCATE {cols} RESTART IDENTITY CASCADE")


def run(dsn: str, *, run_tag: str) -> dict:
    clock = lambda: FIXED_OBSERVATION  # noqa: E731 (fixed clock for reproducibility)
    calendar = TradingCalendar("XNYS")
    object_store = object_store_from_env()
    store = GovernanceStore(dsn, clock=clock)
    request = _cohort(run_tag)

    def runner(overrides=None) -> IngestionRunner:
        adapter = MassiveAdapter(calendar=calendar, clock=clock, fixture_overrides=overrides)
        return IngestionRunner(
            store=store, object_store=object_store, adapter=adapter, calendar=calendar
        )

    v1 = runner().ingest(request)
    gov_before = len(store.governance_events())
    refetch = runner().ingest(request)
    gov_after = len(store.governance_events())
    v2 = runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}}).ingest(request)

    original_retrievable = object_store.exists(v1.object_path)
    original_bytes = object_store.get(v1.object_path)
    v2_bytes = object_store.get(v2.object_path)

    seal_no_m4 = runner().attempt_seal(v1.dataset_build_id)
    seal_massive_only = runner().attempt_seal(
        v1.dataset_build_id,
        reconciliation=ReconciliationEvidence(
            dataset_source="massive", sources=frozenset({"massive"}), passed=True
        ),
    )
    restatements = store.governance_events(event_type=GovernanceEventType.RESTATEMENT_DETECTED)
    refetch_audits = len(store.audit_events(action="identical_refetch"))

    return {
        "mode": "INFRA",
        "vendor": "fixture",
        "note": "Real infrastructure proof; vendor side is fixtures, not live Massive.",
        "object_store": type(object_store).__name__,
        "control_plane": "postgres",
        "cohort": {
            "symbols": list(request.normalized_symbols()),
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "dataset": request.dataset,
        },
        "vintage_1": {
            "sha256": v1.sha256,
            "object_path": v1.object_path,
            "row_count": v1.row_count,
            "state": v1.state.value if v1.state else None,
            "manifest_digest": v1.manifest_digest,
        },
        "identical_rerun": {
            "is_identical_refetch": refetch.is_identical_refetch,
            "governance_events_added": gov_after - gov_before,
            "operational_audit_events": refetch_audits,
        },
        "vintage_2_restatement": {
            "sha256": v2.sha256,
            "object_path": v2.object_path,
            "vintage": v2.vintage,
            "is_restatement": v2.is_restatement,
            "restatement_events": len(restatements),
        },
        "original_vintage": {
            "retrievable": original_retrievable,
            "bytes_identical_to_readback": original_bytes == object_store.get(v1.object_path),
            "distinct_from_vintage_2": (v1.object_path != v2.object_path)
            and (original_bytes != v2_bytes),
        },
        "seal_guard": {
            "seal_without_m4_succeeded": seal_no_m4,
            "seal_with_massive_only_recon_succeeded": seal_massive_only,
        },
        "acceptance": {
            "flow_reaches_provisional": v1.state is not None and v1.state.value == "PROVISIONAL",
            "identical_rerun_no_duplicate": refetch.is_identical_refetch,
            "identical_rerun_no_governance_event": (gov_after == gov_before)
            and refetch_audits == 1,
            "changed_history_new_vintage": v2.is_restatement and v2.vintage == 2,
            "original_vintage_preserved": original_retrievable and v1.object_path != v2.object_path,
            "restatement_recorded": len(restatements) == 1,
            "single_source_cannot_be_sealed": (not seal_no_m4) and (not seal_massive_only),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M0 real-infrastructure proof (fixture vendor).")
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("BACULUS_INFRA_ADMIN_URL"),
        help="Postgres DSN for the real control plane (e.g. Supabase session pooler).",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--run-tag", default="", help="Namespace the dataset to avoid collisions.")
    parser.add_argument(
        "--reset", action="store_true", help="TRUNCATE baculus.* before running (dev DB only)."
    )
    parser.add_argument(
        "--apply-migrations",
        action="store_true",
        help="Apply migrations to --admin-url first (local self-test only; Supabase uses db push).",
    )
    args = parser.parse_args()

    if not args.admin_url:
        print(
            "REFUSING: no Postgres DSN. Pass --admin-url or set BACULUS_INFRA_ADMIN_URL "
            "to the real Supabase Postgres connection string.",
            file=sys.stderr,
        )
        return 2

    if args.apply_migrations:
        apply_migrations(args.admin_url)
    if args.reset:
        _reset(args.admin_url)

    evidence = run(args.admin_url, run_tag=args.run_tag)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)

    acceptance = evidence["acceptance"]
    all_pass = all(acceptance.values())
    print("\n=== M0 INFRASTRUCTURE ACCEPTANCE ===", file=sys.stderr)
    for k, v in acceptance.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}", file=sys.stderr)
    print(f"object store: {evidence['object_store']}", file=sys.stderr)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
