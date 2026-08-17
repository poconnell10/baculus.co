#!/usr/bin/env python3
"""Fail closed if anything that looks like a committed secret is present.

Deterministic, dependency-free guard used in CI and before completion. It checks
that:
  * no real ``.env`` file is tracked (only ``.env.example`` is allowed);
  * the anticipated secret env vars are never assigned a value in tracked files
    (``.env.example`` must keep them empty);
  * no private-key block is committed.

This is a coarse safety net, not a substitute for a dedicated secret scanner.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories that never contain source we control.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "_data",
    "data",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
}

SECRET_ENV_KEYS = (
    "MASSIVE_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_URL",
    "HEARTBEAT_URL",
    "MASSIVE_BASE_URL",
)
# Matches KEY=<non-empty, non-placeholder> assignments.
_ASSIGN = re.compile(r"^\s*(?:export\s+)?(" + "|".join(SECRET_ENV_KEYS) + r")\s*=\s*(?P<val>\S.*)$")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")

# Values in .env.example are allowed to be non-empty ONLY if they are obvious
# non-secret local defaults.
_ALLOWED_EXAMPLE_VALUES = {"./_data", "fixture"}


def _is_placeholder(value: str) -> bool:
    """Whether an assignment value is an obvious doc placeholder, not a secret."""
    v = value.strip().strip("\"'")
    if not v:
        return True
    if "..." in v:
        return True
    return v[:1] in {"<", "$", "{"} or v.lower().startswith(("your", "xxx", "changeme"))


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        out.append(path)
    return out


def scan() -> list[str]:
    problems: list[str] = []

    # A tracked real .env is never allowed.
    for env in REPO_ROOT.rglob(".env"):
        if ".venv" in env.parts:
            continue
        problems.append(f"tracked real env file present: {env.relative_to(REPO_ROOT)}")

    for path in _iter_files():
        rel = path.relative_to(REPO_ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary / unreadable — skip

        is_example = path.name == ".env.example"
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = _ASSIGN.match(line)
            if m:
                value = m.group("val").strip()
                if is_example and value in _ALLOWED_EXAMPLE_VALUES:
                    continue
                if _is_placeholder(value):
                    continue  # doc/example placeholder, not a real secret
                if not is_example or value:
                    problems.append(f"{rel}:{lineno}: {m.group(1)} assigned a value")
            if _PRIVATE_KEY.search(line):
                problems.append(f"{rel}:{lineno}: private key block detected")

    return problems


def main() -> int:
    problems = scan()
    if problems:
        print("SECRET SCAN FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
