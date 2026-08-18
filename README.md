# Baculus

Point-in-time investment research and signal-validation platform.

Baculus's first priority is **trustworthy data provenance**: deterministic
validation, reproducibility, and a strict separation between raw vendor data and
any downstream research output. This repository currently implements **M0 —
Vendor & Infrastructure Foundation**.

> **Status:** M0 (foundation only). No strategy logic, momentum, portfolio
> construction, backtesting, optimization, or execution is implemented, by
> design. See [`docs/architecture/M0.md`](docs/architecture/M0.md).

## Core research invariant: two time axes

Baculus never conflates:

1. **Event time** — when the market event occurred.
2. **Observation time** — when Baculus received *that version* of the data from
   the vendor.

The system can answer *"what did Baculus actually know at time T?"*. Vendor
redeliveries that change history create a **new vintage**; prior observations are
never overwritten.

## Architecture (fixed for M0)

| Concern | Choice |
| --- | --- |
| Language | Python 3.12 |
| DataFrames | Polars |
| Columnar files | PyArrow / Parquet |
| Primary market-data vendor | Massive (behind an adapter) |
| Control / governance plane | Supabase Postgres |
| Object store (POC) | Supabase Storage (local filesystem mirror in dev) |
| CI / orchestration | GitHub Actions |
| Future UI | Next.js / React / TypeScript on Vercel (reserved) |

No FastAPI, Kafka, Airflow, Redis, warehouse, or Kubernetes. This is a POC
architecture; infrastructure is added only with evidence that it is needed.

## Repository layout

```
engine/            Python engine (import name: baculus)
  baculus/
    adapters/      Vendor adapters; Massive-specific code lives ONLY here
    ingestion/     Fetch → hash → store → vintage orchestration
    storage/       Content-addressed raw + object storage
    validation/    Deterministic market-data integrity rules
    normalization/ Vendor-neutral canonical frame construction
    reference/      Authoritative XNYS trading calendar (versioned)
    lineage/        Deterministic manifests + secondary GitHub anchor
    governance/     Control plane, dataset state machine, append-only records
    models/         Canonical daily-bar model + enums + state definitions
  tests/
config/            sources / calendars / validation configuration
schemas/           JSON Schemas for canonical artifacts
supabase/migrations/  Postgres control-plane DDL (the single control-plane schema)
scripts/           prove_m0.py end-to-end evidence generator
docs/              architecture/, decisions/ (ADRs), evidence/
```

## Quickstart

The control plane is Postgres — there is no SQLite fallback. You need a Postgres
the test role can create/drop databases from (a local container is fine):

```bash
docker run -d --name baculus-pg -p 5432:5432 \
  -e POSTGRES_USER=baculus -e POSTGRES_PASSWORD=baculus -e POSTGRES_DB=postgres \
  postgres:16
export BACULUS_TEST_ADMIN_URL=postgresql://baculus:baculus@localhost:5432/postgres
export BACULUS_PROOF_ADMIN_URL=$BACULUS_TEST_ADMIN_URL

uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[dev]'

# Run the full M0 test cohort (against real Postgres)
pytest

# Generate end-to-end M0 evidence (fixtures; no vendor access needed)
python scripts/prove_m0.py --out docs/evidence/m0_proof.json

# Real-infrastructure proof: fixture vendor -> REAL Supabase Storage + Postgres
#   (set env + point --admin-url at the Supabase Postgres DSN)
BACULUS_OBJECT_STORE=supabase python scripts/verify_infra.py \
  --admin-url "$SUPABASE_DB_URL" --out docs/evidence/m0_infra_proof.json

# Verify the LIVE vendor stack (requires MASSIVE_API_KEY, optional Supabase env)
python scripts/verify_live.py --out docs/evidence/live_proof.json
```

Proof drivers, by scope: `prove_m0.py` = local fixture proof (Postgres);
`verify_infra.py` = real Supabase Storage + Postgres with fixture vendor (M0
infrastructure closure); `verify_live.py` = live Massive (fails closed without a
key). None presents fixture output as live-vendor proof.

Configuration comes from environment variables; copy `.env.example` to `.env`.
No secrets are ever committed or logged.

## What M0 proves

A known Massive historical dataset can be ingested, content-hashed, preserved and
validated; repeating an identical ingestion does not duplicate the underlying
market data; changing historical source content creates a separate observable
vintage without overwriting the first; the restatement is recorded as a
governance event; and the resulting **single-source** dataset **cannot** be
promoted to `SEALED` (that requires an independent M4 reconciliation).
