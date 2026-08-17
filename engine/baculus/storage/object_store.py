"""Object-store abstraction and a local filesystem implementation.

The interface is deliberately tiny and content-addressing-friendly. A future
Supabase Storage implementation satisfies the same contract; nothing above this
layer should care which backend is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ObjectStore(ABC):
    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def put(self, path: str, data: bytes) -> bool:
        """Store ``data`` at ``path``.

        Returns ``True`` if a new object was written, ``False`` if an object was
        already present. Implementations backing a content-addressed store MUST
        never overwrite existing bytes with different content.
        """

    @abstractmethod
    def get(self, path: str) -> bytes: ...


class LocalObjectStore(ObjectStore):
    """Filesystem-backed object store rooted at ``base_dir``."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _full(self, path: str) -> Path:
        # Normalize and prevent path escape outside the base dir.
        target = (self.base_dir / path).resolve()
        base = self.base_dir.resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"object path escapes store root: {path!r}")
        return target

    def exists(self, path: str) -> bool:
        return self._full(path).is_file()

    def put(self, path: str, data: bytes) -> bool:
        full = self._full(path)
        if full.is_file():
            # Content-addressed invariant: same path must mean same bytes.
            existing = full.read_bytes()
            if existing != data:
                raise ValueError(
                    f"content-addressed collision at {path!r}: existing bytes differ from new bytes"
                )
            return False
        full.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: write to a temp sibling then rename.
        tmp = full.with_suffix(full.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(full)
        return True

    def get(self, path: str) -> bytes:
        return self._full(path).read_bytes()


__all__ = ["LocalObjectStore", "ObjectStore"]
