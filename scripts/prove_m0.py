#!/usr/bin/env python3
"""Generate end-to-end M0 evidence.

Runs the full acceptance flow against deterministic FIXTURES (no vendor/network
access required) and writes a machine-readable evidence file. This is fixture
proof, explicitly labelled as such — it is never presented as live-vendor proof.

Proves, with executable evidence:
  * a known Massive historical dataset can be ingested, content-hashed,
    preserved and validated;
  * repeating an identical ingestion does not duplicate the underlying market
    data (but the run is still audited);
  * changing historical source content creates a separate observable vintage
    without overwriting the first;
  * the restatement is recorded as a governance event;
  * the resulting single-source dataset cannot be promoted to SEALED.

Usage:
    python scripts/prove_m0.py [--out docs/evidence/m0_proof.json] [--data-root DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

# Allow running from a source checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.governance.state_machine import ReconciliationEvidence
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.models.enums import GovernanceEventType
from baculus.reference.calendar import TradingCalendar
from baculus.storage.object_store import LocalObjectStore

FIXED_OBSERVATION = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)
PROOF_REQUEST = SourceRequest(
    dataset="stocks/daily",
    symbols=("SPY", "XLK", "XLE"),
    start=date(2024, 1, 1),
    end=date(2024, 3, 31),
)


def _count(store: GovernanceStore, table: str) -> int:
    return store.connection.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def run_proof(data_root: Path) -> dict:
    clock = lambda: FIXED_OBSERVATION  # noqa: E731 (fixed clock for reproducibility)
    calendar = TradingCalendar("XNYS")
    object_store = LocalObjectStore(data_root / "objects")
    store = GovernanceStore(str(data_root / "control_plane.sqlite"), clock=clock)

    def runner(overrides=None) -> IngestionRunner:
        adapter = MassiveAdapter(calendar=calendar, clock=clock, fixture_overrides=overrides)
        return IngestionRunner(
            store=store, object_store=object_store, adapter=adapter, calendar=calendar
        )

    # 1) First ingestion.
    v1 = runner().ingest(PROOF_REQUEST)
    # 2) Identical refetch.
    refetch = runner().ingest(PROOF_REQUEST)
    artifacts_after_refetch = _count(store, "raw_artifacts")
    # 3) Restatement (a valid changed value for one historical session).
    v2 = runner(overrides={("SPY", "2024-01-02"): {"v": 7_777_777}}).ingest(PROOF_REQUEST)
    artifacts_after_restatement = _count(store, "raw_artifacts")
    # 4) Original vintage still retrievable, byte-identical.
    original_retrievable = object_store.exists(v1.object_path)
    original_bytes_len = len(object_store.get(v1.object_path))
    # 5) Seal attempts on the (single-source) PROVISIONAL build must fail.
    seal_no_m4 = runner().attempt_seal(v1.dataset_build_id)
    seal_massive_only = runner().attempt_seal(
        v1.dataset_build_id,
        reconciliation=ReconciliationEvidence(
            dataset_source="massive", sources=frozenset({"massive"}), passed=True
        ),
    )

    restatement_events = store.governance_events(
        event_type=GovernanceEventType.RESTATEMENT_DETECTED
    )
    seal_blocked_events = store.governance_events(event_type=GovernanceEventType.SEAL_BLOCKED)

    return {
        "mode": "FIXTURE",  # not live-vendor proof
        "note": "Evidence generated from deterministic fixtures, not a live Massive call.",
        "cohort": {
            "symbols": list(PROOF_REQUEST.normalized_symbols()),
            "start": PROOF_REQUEST.start.isoformat(),
            "end": PROOF_REQUEST.end.isoformat(),
        },
        "observation_time": FIXED_OBSERVATION.isoformat(),
        "vintage_1": {
            "sha256": v1.sha256,
            "object_path": v1.object_path,
            "byte_size": v1.byte_size,
            "row_count": v1.row_count,
            "vintage": v1.vintage,
            "state": v1.state.value if v1.state else None,
            "manifest_digest": v1.manifest_digest,
        },
        "identical_refetch": {
            "sha256": refetch.sha256,
            "is_identical_refetch": refetch.is_identical_refetch,
            "is_new_content": refetch.is_new_content,
            "raw_artifacts_after": artifacts_after_refetch,
            "ingestion_runs": _count(store, "ingestion_runs"),
            "raw_observations": _count(store, "raw_observations"),
        },
        "vintage_2_restatement": {
            "sha256": v2.sha256,
            "object_path": v2.object_path,
            "vintage": v2.vintage,
            "is_restatement": v2.is_restatement,
            "raw_artifacts_after": artifacts_after_restatement,
            "restatement_events": len(restatement_events),
        },
        "original_vintage": {
            "retrievable": original_retrievable,
            "bytes": original_bytes_len,
            "distinct_from_vintage_2": v1.object_path != v2.object_path,
        },
        "seal_guard": {
            "seal_without_m4_succeeded": seal_no_m4,  # expected False
            "seal_with_massive_only_recon_succeeded": seal_massive_only,  # expected False
            "seal_blocked_events": len(seal_blocked_events),
        },
        "acceptance": {
            "identical_ingestion_no_duplicate": artifacts_after_refetch == 1,
            "restatement_created_new_vintage": artifacts_after_restatement == 2 and v2.vintage == 2,
            "original_vintage_preserved": original_retrievable and v1.object_path != v2.object_path,
            "restatement_recorded": len(restatement_events) == 1,
            "single_source_cannot_be_sealed": (not seal_no_m4) and (not seal_massive_only),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate M0 evidence (fixtures).")
    parser.add_argument("--out", type=Path, default=None, help="Write evidence JSON here.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Directory for the object store + control plane (default: a temp dir).",
    )
    args = parser.parse_args()

    data_root = args.data_root or Path(tempfile.mkdtemp(prefix="baculus_m0_"))
    data_root.mkdir(parents=True, exist_ok=True)

    evidence = run_proof(data_root)
    text = json.dumps(evidence, indent=2, sort_keys=True)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")

    print(text)
    acceptance = evidence["acceptance"]
    all_pass = all(acceptance.values())
    print("\n=== M0 ACCEPTANCE ===", file=sys.stderr)
    for k, v in acceptance.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}", file=sys.stderr)
    print(f"data-root: {data_root}", file=sys.stderr)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
