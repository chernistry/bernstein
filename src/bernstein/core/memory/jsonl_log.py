"""Append-only JSONL memory log for cross-session per-key event recording.

Lightweight complement to :mod:`bernstein.core.memory.sqlite_store`.  Whereas
the SQLite store is a tag-indexed knowledge base (conventions, decisions,
learnings), this module provides a flat *append-only* event log keyed by a
short identifier.  Each key maps to exactly one JSONL file at
``<root>/<key>.jsonl`` (default root: ``.bernstein/memory/``).

Design goals (smallest-viable slice):

- No retrieval / scoring / decay - that lives in the SQLite layer.
- No locking primitives beyond what append-write to a POSIX file gives us;
  callers needing strict cross-process serialisation should use the SQLite
  store instead.
- Pure ``json`` + ``pathlib``; no third-party deps; no DB schema migrations.
- Off-by-default: nothing in the orchestrator reads or writes here yet.
  Spawner-injection wiring is deferred.

Typical usage::

    from bernstein.core.memory.jsonl_log import JSONLMemoryLog

    log = JSONLMemoryLog(root=Path(".bernstein/memory"))
    log.write("manager.lessons", {"task": "T-1", "lesson": "guard imports"})
    entries = log.read("manager.lessons")  # list[dict]

The on-disk format is one JSON object per line.  Malformed lines (truncated
writes, manual edits) are skipped on read with a warning rather than raising,
so a corrupted tail cannot brick the whole log.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Keys map directly to filenames; restrict to a conservative POSIX-safe set
# so a poisoned key cannot escape ``root`` (path traversal) or collide with
# OS-reserved characters.  Dots, dashes, underscores allowed: a key like
# ``"manager.lessons"`` becomes ``manager.lessons.jsonl``.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_KEY_LEN = 128


def _validate_key(key: str) -> None:
    """Reject keys that could escape the memory root or break the filesystem.

    Args:
        key: User-supplied memory key.

    Raises:
        ValueError: Key is empty, too long, or contains disallowed characters.
    """
    if not key or len(key) > _MAX_KEY_LEN:
        raise ValueError(f"memory key must be 1..{_MAX_KEY_LEN} chars, got len={len(key)}")
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            f"memory key {key!r} must match {_KEY_RE.pattern!r} "
            "(alphanumerics + . _ - only, must start with alphanumeric)"
        )


@dataclass(frozen=True)
class JSONLMemoryLog:
    """Append-only per-key JSONL log under a single root directory.

    Attributes:
        root: Directory holding ``<key>.jsonl`` files.  Created on first
            write if missing.
    """

    root: Path

    def _path(self, key: str) -> Path:
        """Return the on-disk path for *key*, validating it first."""
        _validate_key(key)
        return self.root / f"{key}.jsonl"

    def write(self, key: str, entry: dict[str, Any]) -> None:
        """Append *entry* (a JSON-serialisable dict) to the log for *key*.

        Args:
            key: Memory key (alphanumeric + ``._-``).
            entry: A dict that ``json.dumps`` can serialise.

        Raises:
            ValueError: ``key`` fails validation.
            TypeError: ``entry`` is not a dict or contains non-serialisable
                values.
        """
        if not isinstance(entry, dict):
            raise TypeError(f"entry must be a dict, got {type(entry).__name__}")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``ensure_ascii=False`` keeps unicode readable; compact separators
        # mean a tail-corruption only loses one record.
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")

    def read(self, key: str) -> list[dict[str, Any]]:
        """Return all entries previously written under *key*, oldest first.

        Args:
            key: Memory key.

        Returns:
            List of dict entries.  Empty list if the key has never been
            written.  Malformed lines are skipped (and logged at warning
            level) so a partial tail-write cannot brick the whole log.

        Raises:
            ValueError: ``key`` fails validation.
        """
        path = self._path(key)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.warning("skipping malformed JSONL line %s:%d (%s)", path, lineno, exc)
                    continue
                if not isinstance(parsed, dict):
                    logger.warning(
                        "skipping non-dict JSONL entry %s:%d (got %s)",
                        path,
                        lineno,
                        type(parsed).__name__,
                    )
                    continue
                entries.append(parsed)
        return entries

    def list_keys(self) -> list[str]:
        """Return all keys currently present on disk, lexicographically sorted."""
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.jsonl") if p.is_file())

    def clear(self, key: str) -> bool:
        """Delete the JSONL file for *key*.

        Args:
            key: Memory key.

        Returns:
            ``True`` if a file was removed, ``False`` if the key did not
            exist.

        Raises:
            ValueError: ``key`` fails validation.
        """
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False
