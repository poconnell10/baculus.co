"""Deterministic / canonical serialization and content hashing.

Governance and dataset manifests must be *content-addressable*: the same logical
content must always produce the same SHA-256 digest, regardless of dict ordering
or the machine that produced it. This module provides that canonical form.

Rules:
  * dict keys are sorted; ``ensure_ascii=False``; no insignificant whitespace;
  * lists preserve order (order is semantic — callers sort where needed);
  * ``date`` / ``datetime`` become ISO-8601 strings (UTC datetimes use ``Z``);
  * ``Enum`` becomes its value; ``Decimal`` becomes its canonical string;
  * unknown types raise (fail closed) rather than being coerced silently.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

JSONScalar = str | int | float | bool | None


def _normalize(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-canonical-safe primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        # NB: bool is a subclass of int; both are fine and deterministic.
        return value
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Decimal):
        # Canonical, lossless string form.
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        # Normalize to UTC and emit with a trailing 'Z' for stability.
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed in canonical content")
        utc = value.astimezone(UTC)
        return utc.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical dict keys must be str, got {type(k)!r}")
            out[k] = _normalize(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    raise TypeError(f"type {type(value)!r} is not canonically serializable")


def to_canonical_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical UTF-8 JSON bytes."""
    normalized = _normalize(obj)
    text = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def digest_canonical(obj: Any) -> str:
    """Return the SHA-256 hex digest of ``obj``'s canonical serialization."""
    return sha256_hex(to_canonical_bytes(obj))


__all__ = [
    "digest_canonical",
    "sha256_hex",
    "to_canonical_bytes",
]
