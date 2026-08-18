#!/usr/bin/env python3
"""Live Massive M0 proof: REAL vendor, REAL storage, REAL Postgres control plane.

This is the proof that closes M0 with a real market-data source. Unlike
``verify_infra.py`` (whose vendor side is deterministic fixtures), this driver
calls the LIVE Massive REST API through the exact production adapter/HTTP client
and runs the full ingestion path end to end:

  * authenticate (Bearer header) and look up AAPL reference (auth proof),
  * fetch AAPL daily bars for 2020-01-01..2020-01-31 (adjusted) — retaining every
    page verbatim in the aggregates envelope — normalize into canonical bars, and
    validate deterministically to PROVISIONAL,
  * replay the same fetch and prove it is NOT a restatement / new economic vintage
    (ADR-0010: identity is logical, not raw-byte),
  * prove a single Massive-only build can never be SEALED (needs independent M4).

REQUIRES ``MASSIVE_API_KEY`` (live). It REFUSES to run without it rather than
silently degrade — an offline pass is not live-vendor evidence. The secret is
read from the environment and never printed, logged, or persisted.

NON-DESTRUCTIVE BY DESIGN. It never truncates or deletes. The run is isolated
under a unique ``proof_run_id`` that namespaces the dataset (and therefore the
object-store paths and vintaging logical key), so re-runs never collide.

Usage (Mac, with .env loaded and Supabase migrated via `supabase db push`):
    set -a && source .env && set +a
    export BACULUS_OBJECT_STORE=supabase
    python scripts/verify_massive.py --admin-url "$DATABASE_URL" \
        --out docs/evidence/m0_massive_live_proof.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.governance.migrations import apply_migrations
from baculus.governance.state_machine import ReconciliationEvidence
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.models.enums import FetchMode, GovernanceEventType
from baculus.reference.calendar import TradingCalendar
from baculus.storage import object_store_from_env

_FROM, _TO = date(2020, 1, 1), date(2020, 1, 31)


def _request(proof_run_id: str) -> SourceRequest:
    # proof_run_id namespaces the dataset so every run is isolated: distinct
    # logical key (no unique-vintage collision) and distinct object-store paths.
    return SourceRequest(
        dataset=f"stocks/daily/massive-live-{proof_run_id}",
        symbols=("AAPL",),
        start=_FROM,
        end=_TO,
        options=(("adjusted", "true"),),
    )


def run(dsn: str, *, proof_run_id: str, capture_dir: Path | None = None) -> dict:
    calendar = TradingCalendar("XNYS")
    object_store = object_store_from_env()
    store = GovernanceStore(dsn, clock=lambda: datetime.now(UTC))
    request = _request(proof_run_id)

    def make_adapter() -> MassiveAdapter:
        # Real observation time per fetch; LIVE mode calls the vendor API.
        return MassiveAdapter(
            mode=FetchMode.LIVE, calendar=calendar, clock=lambda: datetime.now(UTC)
        )

    def runner() -> IngestionRunner:
        return IngestionRunner(
            store=store, object_store=object_store, adapter=make_adapter(), calendar=calendar
        )

    # 1) Auth + reference lookup (never fetches bars).
    reference = make_adapter().reference_lookup("AAPL")
    reference_ticker = str(reference.get("ticker", "")).upper()

    # 2) Full ingest to PROVISIONAL.
    v1 = runner().ingest(request)

    # 3) Replay: same window, live vendor re-fetch. Different vendor request id =>
    #    different raw bytes but identical logical data => raw-only change, NOT a
    #    restatement and NOT a new economic vintage (ADR-0010).
    gov_before = len(store.governance_events())
    replay = runner().ingest(request)
    gov_after = len(store.governance_events())

    replay_restatements = store.connection.execute(
        "SELECT count(*) AS c FROM governance_events WHERE event_type = %s AND subject_id = %s",
        (GovernanceEventType.RESTATEMENT_DETECTED.value, replay.sha256),
    ).fetchone()["c"]

    # 4) Seal guard: a single Massive-only build can never be SEALED.
    seal_no_m4 = runner().attempt_seal(v1.dataset_build_id) if v1.dataset_build_id else False
    seal_massive_only = (
        runner().attempt_seal(
            v1.dataset_build_id,
            reconciliation=ReconciliationEvidence(
                dataset_source="massive", sources=frozenset({"massive"}), passed=True
            ),
        )
        if v1.dataset_build_id
        else False
    )

    key = os.environ.get("MASSIVE_API_KEY", "")
    raw_bytes = object_store.get(v1.object_path)
    secret_absent = bool(key) and key.encode("utf-8") not in raw_bytes

    # Capture the retained raw evidence (the verbatim aggregates envelope) so the
    # ADR-0009 vendor-semantics questions can be answered from real bytes. It is
    # verified secret-free above before being written.
    if capture_dir is not None and secret_absent:
        capture_dir.mkdir(parents=True, exist_ok=True)
        (capture_dir / "massive_raw_sample.json").write_bytes(raw_bytes)

    replay_not_restatement = (
        (not replay.is_restatement)
        and replay.vintage == v1.vintage
        and replay_restatements == 0
        and (replay.logical_data_sha256 == v1.logical_data_sha256 or replay.is_identical_refetch)
    )

    return {
        "mode": "LIVE",
        "vendor": "massive",
        "note": "Live Massive vendor proof; real API key, real storage, real Postgres.",
        "proof_run_id": proof_run_id,
        "isolation": "non-destructive; run namespaced by proof_run_id; nothing truncated/deleted",
        "object_store": type(object_store).__name__,
        "control_plane": "postgres",
        "request": {
            "symbol": "AAPL",
            "start": _FROM.isoformat(),
            "end": _TO.isoformat(),
            "adjusted": True,
            "dataset": request.dataset,
        },
        "reference_lookup": {
            "ticker": reference_ticker,
            "name": reference.get("name"),
            "authenticated": reference_ticker == "AAPL",
        },
        "vintage_1": {
            "sha256": v1.sha256,
            "logical_data_sha256": v1.logical_data_sha256,
            "object_path": v1.object_path,
            "row_count": v1.row_count,
            "state": v1.state.value if v1.state else None,
            "manifest_digest": v1.manifest_digest,
            "quarantined": v1.quarantined,
        },
        "replay": {
            "sha256": replay.sha256,
            "is_identical_refetch": replay.is_identical_refetch,
            "is_raw_only_change": replay.is_raw_only_change,
            "is_restatement": replay.is_restatement,
            "vintage": replay.vintage,
            "governance_events_added": gov_after - gov_before,
            "restatement_events": replay_restatements,
        },
        "seal_guard": {
            "seal_without_m4_succeeded": seal_no_m4,
            "seal_with_massive_only_recon_succeeded": seal_massive_only,
        },
        "acceptance": {
            "auth_and_reference_ok": reference_ticker == "AAPL",
            "flow_reaches_provisional": v1.state is not None and v1.state.value == "PROVISIONAL",
            "not_quarantined": not v1.quarantined,
            "canonical_rows_present": bool(v1.row_count and v1.row_count > 0),
            "raw_secret_absent_from_payload": secret_absent,
            "replay_not_a_restatement": replay_not_restatement,
            "single_source_cannot_be_sealed": (not seal_no_m4) and (not seal_massive_only),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Massive M0 proof (real vendor).")
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("BACULUS_INFRA_ADMIN_URL") or os.environ.get("DATABASE_URL"),
        help="Postgres DSN for the real control plane (read from env; never printed).",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--capture-dir",
        type=Path,
        default=None,
        help="If set, write the retained raw aggregates envelope here "
        "(massive_raw_sample.json) for ADR-0009 analysis. Verified secret-free first.",
    )
    parser.add_argument(
        "--proof-run-id",
        default=None,
        help="Isolation namespace for this run (default: a fresh random id). "
        "Each run is isolated; nothing is ever truncated or deleted.",
    )
    parser.add_argument(
        "--apply-migrations",
        action="store_true",
        help="Apply migrations to --admin-url first (local self-test only; Supabase uses db push).",
    )
    args = parser.parse_args()

    if not os.environ.get("MASSIVE_API_KEY"):
        print(
            "REFUSING: MASSIVE_API_KEY is not set. This is the LIVE vendor proof; an offline "
            "run is not live evidence. Load your (uncommitted) .env first. The key is never "
            "printed.",
            file=sys.stderr,
        )
        return 2
    if not args.admin_url:
        print(
            "REFUSING: no Postgres DSN. Set DATABASE_URL / BACULUS_INFRA_ADMIN_URL in your "
            "(uncommitted) .env, or pass --admin-url. The value is never printed.",
            file=sys.stderr,
        )
        return 2

    proof_run_id = args.proof_run_id or uuid.uuid4().hex[:12]
    if args.apply_migrations:
        apply_migrations(args.admin_url)

    evidence = run(args.admin_url, proof_run_id=proof_run_id, capture_dir=args.capture_dir)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)

    acceptance = evidence["acceptance"]
    all_pass = all(acceptance.values())
    print("\n=== M0 LIVE MASSIVE ACCEPTANCE ===", file=sys.stderr)
    for k, v in acceptance.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}", file=sys.stderr)
    print(
        f"proof_run_id: {proof_run_id} | object store: {evidence['object_store']}",
        file=sys.stderr,
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
