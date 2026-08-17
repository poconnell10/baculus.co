#!/usr/bin/env python3
"""Live-stack verification: Massive -> object store -> Postgres control plane.

Runs the SAME M0 acceptance flow as scripts/prove_m0.py, but against the REAL
stack instead of fixtures:

  * live Massive (LIVE fetch mode, using MASSIVE_API_KEY);
  * the env-selected object store (Supabase Storage when BACULUS_OBJECT_STORE=
    supabase, else local);
  * a real Postgres control plane.

It also captures the first raw Massive response to ``docs/evidence/`` so the
vendor-semantics questions in ADR-0009 can be answered from real bytes.

This script FAILS CLOSED if MASSIVE_API_KEY is absent — it never fabricates
live-vendor evidence. Emitted evidence is tagged ``"mode": "LIVE"`` so it is
never confused with fixture proof, and its raw content hashes are directly
comparable to the fixture proof's structure.

Usage: set the vendor/Supabase/Postgres environment (see .env.example), then run
``python scripts/verify_live.py --out docs/evidence/live_proof.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

import psycopg

from baculus.adapters.base import SourceRequest
from baculus.adapters.massive import MassiveAdapter
from baculus.governance.migrations import apply_migrations
from baculus.governance.state_machine import ReconciliationEvidence
from baculus.governance.store import GovernanceStore
from baculus.ingestion.runner import IngestionRunner
from baculus.models.enums import FetchMode
from baculus.reference.calendar import TradingCalendar
from baculus.storage import object_store_from_env

_LIVE_DB = "baculus_live"
COHORT = SourceRequest(
    dataset="stocks/daily",
    symbols=("SPY", "XLK", "XLE"),
    start=date(2024, 1, 1),
    end=date(2024, 3, 31),
)


def _provision(admin_url: str) -> str:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{_LIVE_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{_LIVE_DB}"')
    parts = urlsplit(admin_url)
    dsn = urlunsplit(parts._replace(path=f"/{_LIVE_DB}"))
    apply_migrations(dsn)
    return dsn


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the live M0 stack.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--admin-url",
        default=os.environ.get(
            "BACULUS_LIVE_ADMIN_URL", "postgresql://baculus:baculus@localhost:5432/postgres"
        ),
    )
    parser.add_argument("--capture-dir", type=Path, default=Path("docs/evidence"))
    args = parser.parse_args()

    if not os.environ.get("MASSIVE_API_KEY"):
        print(
            "REFUSING: MASSIVE_API_KEY is not set. This script proves the LIVE stack "
            "and never fabricates live-vendor evidence. Provide the key to run.",
            file=sys.stderr,
        )
        return 2

    calendar = TradingCalendar("XNYS")
    object_store = object_store_from_env()
    dsn = _provision(args.admin_url)
    store = GovernanceStore(dsn)
    adapter = MassiveAdapter(mode=FetchMode.LIVE, calendar=calendar)

    # Capture the raw vendor response for ADR-0009 vendor-semantics analysis.
    raw = adapter.fetch(COHORT)
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    (args.capture_dir / "massive_raw_sample.json").write_bytes(raw.payload)

    runner = IngestionRunner(
        store=store, object_store=object_store, adapter=adapter, calendar=calendar
    )
    first = runner.ingest(COHORT)
    refetch = runner.ingest(COHORT)
    seal_no_m4 = runner.attempt_seal(first.dataset_build_id)
    seal_ok = runner.attempt_seal(
        first.dataset_build_id,
        reconciliation=ReconciliationEvidence(
            dataset_source="massive", sources=frozenset({"massive"}), passed=True
        ),
    )

    evidence = {
        "mode": "LIVE",
        "object_store": type(object_store).__name__,
        "cohort": {
            "symbols": list(COHORT.normalized_symbols()),
            "start": COHORT.start.isoformat(),
            "end": COHORT.end.isoformat(),
        },
        "vintage_1": {
            "sha256": first.sha256,
            "object_path": first.object_path,
            "row_count": first.row_count,
            "state": first.state.value if first.state else None,
            "manifest_digest": first.manifest_digest,
        },
        "identical_refetch": {
            "is_identical_refetch": refetch.is_identical_refetch,
            "operational_audit_events": len(store.audit_events(action="identical_refetch")),
        },
        "acceptance": {
            "ingested_provisional": first.state is not None and first.state.value == "PROVISIONAL",
            "identical_refetch_no_duplicate": refetch.is_identical_refetch,
            "single_source_cannot_be_sealed": (not seal_no_m4) and (not seal_ok),
        },
    }
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(
        "\nNOTE: analyze docs/evidence/massive_raw_sample.json to answer ADR-0009 "
        "(adjusted/unadjusted, splits/dividends, pagination, identity, timestamps, corrections).",
        file=sys.stderr,
    )
    return 0 if all(evidence["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
