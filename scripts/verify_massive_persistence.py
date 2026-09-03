#!/usr/bin/env python3
"""Read-only persistence/provenance evidence for a live Massive M0 proof run.

Companion to ``scripts/verify_massive.py``. Given the ``proof_run_id`` of a live
run, this driver connects to the SAME Postgres control plane and, using ONLY
read-only SELECTs, proves that the run persisted what the M0 architecture
expects:

  * live ingestion run(s) recorded (fetch_mode = 'live');
  * one immutable raw artifact per symbol (content-addressed sha256), with the
    logical-data identity (ADR-0010) and object-store path;
  * provenance linkage raw_observation -> ingestion_run -> raw_artifact;
  * a dataset build per symbol at PROVISIONAL, with the canonical parquet + the
    manifest persisted to object storage (Postgres holds metadata only — the
    normalized OHLC bodies live in object storage, never in SQL);
  * deterministic validation PASSED;
  * per-symbol dataset-namespace isolation (no cross-symbol collision);
  * no restatement raised by the live run;
  * prior proof runs remain present (this run coexists, nothing removed).

STRICTLY READ-ONLY. The connection is put in read-only mode
(``default_transaction_read_only = on``); any write would raise. It never
TRUNCATEs, DELETEs, resets, or alters. It reads no secrets and prints none.

Usage (Mac, with .env loaded — the DSN is read from env and never printed):
    python scripts/verify_massive_persistence.py --proof-run-id <id> \
        --admin-url "$DATABASE_URL" --out docs/evidence/m0_massive_persistence.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

_RESTATEMENT = "restatement_detected"


def _namespace_like(proof_run_id: str) -> str:
    # verify_massive.py namespaces each symbol as
    #   stocks/daily/massive-live-{proof_run_id}/{SYMBOL}
    return f"stocks/daily/massive-live-{proof_run_id}/%"


def _connect_readonly(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    # Hard read-only guarantee: every implicit transaction is read-only, so any
    # INSERT/UPDATE/DELETE/DDL from this process raises rather than mutating.
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET search_path TO baculus, public")
    return conn


def collect(conn: psycopg.Connection, proof_run_id: str) -> dict[str, Any]:
    like = _namespace_like(proof_run_id)
    q = lambda sql, *p: conn.execute(sql, p).fetchall()  # noqa: E731

    runs = q(
        "SELECT id::text, dataset, fetch_mode, observation_time::text, status "
        "FROM ingestion_runs WHERE dataset LIKE %s ORDER BY dataset, observation_time",
        like,
    )
    artifacts = q(
        "SELECT id AS sha256, dataset, source_id, logical_key, vintage, "
        "logical_data_sha256, object_path, row_count, fetch_mode, "
        "event_date_min::text, event_date_max::text "
        "FROM raw_artifacts WHERE dataset LIKE %s ORDER BY dataset, vintage",
        like,
    )
    observations = q(
        "SELECT artifact_id, run_id::text, dataset, is_new_content, observation_time::text "
        "FROM raw_observations WHERE dataset LIKE %s ORDER BY dataset",
        like,
    )
    builds = q(
        "SELECT id::text, dataset, logical_key, vintage, state, manifest_digest "
        "FROM dataset_builds WHERE dataset LIKE %s ORDER BY dataset",
        like,
    )
    dataset_artifacts = q(
        "SELECT da.kind, da.object_path, da.sha256, db.dataset "
        "FROM dataset_artifacts da JOIN dataset_builds db ON da.dataset_build_id = db.id "
        "WHERE db.dataset LIKE %s ORDER BY db.dataset, da.kind",
        like,
    )
    validations = q(
        "SELECT vr.status, vr.error_count, vr.warning_count, db.dataset "
        "FROM validation_runs vr JOIN dataset_builds db ON vr.dataset_build_id = db.id "
        "WHERE db.dataset LIKE %s ORDER BY db.dataset",
        like,
    )
    artifact_ids = [a["sha256"] for a in artifacts]
    if artifact_ids:
        restatements = q(
            "SELECT count(*) AS c FROM governance_events "
            "WHERE event_type = %s AND subject_id = ANY(%s)",
            _RESTATEMENT,
            artifact_ids,
        )[0]["c"]
    else:
        restatements = 0
    # Coexistence: other live proof-run namespaces still present (nothing removed).
    other_namespaces = q(
        "SELECT DISTINCT split_part(dataset, '/', 3) AS ns FROM raw_artifacts "
        "WHERE dataset LIKE 'stocks/daily/massive-live-%%' AND dataset NOT LIKE %s "
        "ORDER BY ns",
        like,
    )
    return {
        "runs": runs,
        "artifacts": artifacts,
        "observations": observations,
        "builds": builds,
        "dataset_artifacts": dataset_artifacts,
        "validations": validations,
        "restatement_events": restatements,
        "other_live_namespaces": [r["ns"] for r in other_namespaces],
    }


def evaluate(data: dict[str, Any], expected_symbols: tuple[str, ...] | None) -> dict[str, Any]:
    artifacts = data["artifacts"]
    builds = data["builds"]

    # Symbol = the trailing dataset path segment.
    def _sym(dataset: str) -> str:
        return dataset.rsplit("/", 1)[-1].upper()

    symbols_found = sorted({_sym(a["dataset"]) for a in artifacts})
    obs_artifact_ids = {o["artifact_id"] for o in data["observations"]}
    obs_have_runs = all(o["run_id"] for o in data["observations"])

    builds_by_symbol = {_sym(b["dataset"]): b for b in builds}
    da_by_symbol: dict[str, set[str]] = {}
    for da in data["dataset_artifacts"]:
        da_by_symbol.setdefault(_sym(da["dataset"]), set()).add(da["kind"])

    val_by_symbol = {_sym(v["dataset"]): v for v in data["validations"]}

    expected = (
        tuple(s.upper() for s in expected_symbols) if expected_symbols else tuple(symbols_found)
    )

    gates = {
        "live_runs_present": bool(data["runs"])
        and all(r["fetch_mode"] == "live" for r in data["runs"]),
        "raw_artifacts_live_per_symbol": all(
            any(
                _sym(a["dataset"]) == s and a["fetch_mode"] == "live" and (a["row_count"] or 0) > 0
                for a in artifacts
            )
            for s in expected
        )
        and len(expected) > 0,
        "provenance_linked": (
            bool(artifacts)
            and all(a["sha256"] in obs_artifact_ids for a in artifacts)
            and obs_have_runs
            and all(a["logical_data_sha256"] for a in artifacts)
        ),
        "canonical_and_manifest_persisted": bool(builds)
        and all(
            {"canonical_parquet", "manifest"}.issubset(da_by_symbol.get(s, set())) for s in expected
        ),
        "validation_passed": bool(val_by_symbol)
        and all(
            val_by_symbol.get(s, {}).get("status") == "PASSED"
            and (val_by_symbol.get(s, {}).get("error_count") or 0) == 0
            for s in expected
        ),
        "reached_provisional": bool(builds_by_symbol)
        and all(builds_by_symbol.get(s, {}).get("state") == "PROVISIONAL" for s in expected),
        "per_symbol_isolation": (
            len({a["dataset"] for a in artifacts}) == len(expected)
            and len({a["logical_key"] for a in artifacts}) == len(expected)
            and sorted(symbols_found) == sorted(expected)
        ),
        "no_restatement_in_run": data["restatement_events"] == 0,
    }
    return {
        "expected_symbols": list(expected),
        "symbols_found": symbols_found,
        "gates": gates,
        "all_pass": all(gates.values()),
    }


def run(dsn: str, *, proof_run_id: str, expected_symbols: tuple[str, ...] | None) -> dict[str, Any]:
    with _connect_readonly(dsn) as conn:
        data = collect(conn, proof_run_id)
    verdict = evaluate(data, expected_symbols)
    return {
        "mode": "READ_ONLY_PERSISTENCE_EVIDENCE",
        "proof_run_id": proof_run_id,
        "namespace_prefix": f"stocks/daily/massive-live-{proof_run_id}/",
        "note": "Read-only proof that the live Massive run persisted per the M0 architecture.",
        "counts": {
            "ingestion_runs": len(data["runs"]),
            "raw_artifacts": len(data["artifacts"]),
            "raw_observations": len(data["observations"]),
            "dataset_builds": len(data["builds"]),
            "dataset_artifacts": len(data["dataset_artifacts"]),
            "validation_runs": len(data["validations"]),
            "restatement_events": data["restatement_events"],
        },
        "other_live_namespaces_present": data["other_live_namespaces"],
        "evidence": data,
        **verdict,
    }


def _parse_symbols(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in raw.split(","):
        s = part.strip().upper()
        if s and s not in out:
            out.append(s)
    return tuple(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only persistence/provenance evidence for a live Massive M0 proof run."
    )
    parser.add_argument(
        "--proof-run-id", required=True, help="proof_run_id of the live run to verify."
    )
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("BACULUS_INFRA_ADMIN_URL") or os.environ.get("DATABASE_URL"),
        help="Postgres DSN (read from env; never printed).",
    )
    parser.add_argument(
        "--symbols",
        type=_parse_symbols,
        default=None,
        help="Expected cohort (default: derive from persisted datasets).",
    )
    parser.add_argument("--out", type=argparse.FileType("w"), default=None)
    args = parser.parse_args()

    if not args.admin_url:
        print(
            "REFUSING: no Postgres DSN. Set DATABASE_URL / BACULUS_INFRA_ADMIN_URL in your "
            "(uncommitted) .env, or pass --admin-url. The value is never printed.",
            file=sys.stderr,
        )
        return 2

    evidence = run(args.admin_url, proof_run_id=args.proof_run_id, expected_symbols=args.symbols)
    text = json.dumps(evidence, indent=2, sort_keys=True, default=str)
    if args.out is not None:
        args.out.write(text + "\n")
    print(text)

    print("\n=== M0 LIVE PERSISTENCE EVIDENCE ===", file=sys.stderr)
    for k, v in evidence["gates"].items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}", file=sys.stderr)
    _syms = ",".join(evidence["symbols_found"])
    print(
        f"proof_run_id: {evidence['proof_run_id']} | symbols: {_syms}",
        file=sys.stderr,
    )
    return 0 if evidence["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
