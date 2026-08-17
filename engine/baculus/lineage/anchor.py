"""Secondary tamper-evidence anchor.

A manifest digest computed in the control plane can be periodically committed
into GitHub — a *separate provider and trust domain* — so that tampering with
the primary control plane can be detected by comparison.

This is deliberately described as a **secondary tamper-evidence anchor**, not
proof of immutability. Git history is not absolutely immutable (it can be
force-pushed/rewritten by a sufficiently privileged actor); what this provides
is *evidence* in an independent system that must also be compromised for
tampering to go unnoticed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnchorRecord:
    subject_type: str
    subject_id: str
    digest: str
    anchored_at: str


class AnchorLedger:
    """Append-only JSONL ledger of digests, intended to be committed to git."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        subject_type: str,
        subject_id: str,
        digest: str,
        at: datetime | None = None,
    ) -> AnchorRecord:
        at = (at or datetime.now(UTC)).astimezone(UTC)
        record = AnchorRecord(
            subject_type=subject_type,
            subject_id=subject_id,
            digest=digest,
            anchored_at=at.isoformat(),
        )
        line = json.dumps(
            {
                "subject_type": record.subject_type,
                "subject_id": record.subject_id,
                "digest": record.digest,
                "anchored_at": record.anchored_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def read_all(self) -> list[AnchorRecord]:
        if not self.path.is_file():
            return []
        out: list[AnchorRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            doc = json.loads(line)
            out.append(
                AnchorRecord(
                    subject_type=doc["subject_type"],
                    subject_id=doc["subject_id"],
                    digest=doc["digest"],
                    anchored_at=doc["anchored_at"],
                )
            )
        return out


__all__ = ["AnchorLedger", "AnchorRecord"]
