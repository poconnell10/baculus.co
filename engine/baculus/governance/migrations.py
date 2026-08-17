"""Apply the Supabase Postgres migrations.

There is exactly ONE control-plane schema definition: the SQL files under
``supabase/migrations``. This helper applies them in order to a Postgres DSN.
It is used by dev/CI setup, the test harness, and ``scripts/prove_m0.py`` so
that everything runs against the same schema and the same enforcement model.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

# Resolve <repo_root>/supabase/migrations from this file's location, or from an
# explicit override (useful when the package is not run from a source checkout).
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def migrations_dir() -> Path:
    override = os.environ.get("BACULUS_MIGRATIONS_DIR")
    return Path(override) if override else _DEFAULT_MIGRATIONS_DIR


def migration_files() -> list[Path]:
    d = migrations_dir()
    return sorted(d.glob("*.sql"))


def apply_migrations(dsn: str) -> list[str]:
    """Apply all migrations in order to ``dsn``. Returns applied file names."""
    applied: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in migration_files():
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied


__all__ = ["apply_migrations", "migration_files", "migrations_dir"]
