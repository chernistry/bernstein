"""Deterministic session-id binding for adapter replay isolation.

Several adapters let the underlying CLI assign its own session id. Two
failure modes follow:

* A rerun of the orchestrator cannot resume the same conversation because
  the id the CLI minted last time is non-deterministic.
* Two parallel sessions of the same adapter in the same worktree can
  clobber each other's on-disk history files.

This module derives a *deterministic* session id from the orchestrator's
conversation id, namespaced per adapter, so that:

* replay reaches the same conversation slot on rerun (the id is stable
  across processes and across runs), and
* concurrent sessions of distinct adapters never collide (the adapter
  name is mixed into the namespace).

Derivation recipe (documented, stable surface):

    digest = HMAC-SHA256(key=conversation_id, msg="bernstein.adapter:" + adapter_name)
    session_id = UUIDv5-style stamp built from the first 16 digest bytes,
                 with RFC 4122 version (5) and variant bits set.

HMAC is used purely as a keyed, well-defined mixing function with a fixed
namespace string; it is not a security boundary. The recipe is captured in
:data:`NAMESPACE_PREFIX` and :data:`DERIVE_RECIPE_VERSION` so future changes
to the recipe are explicit and versioned.

See ``docs/adapters/session_isolation.md`` for the binding and the replay
benefit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DERIVE_RECIPE_VERSION",
    "NAMESPACE_PREFIX",
    "SESSION_INDEX_FILENAME",
    "SessionIdIndex",
    "SessionIdRecord",
    "derive_session_id",
]

#: Fixed namespace string mixed into every derivation. The adapter name is
#: appended so distinct adapters land in disjoint id spaces even when they
#: share a conversation id.
NAMESPACE_PREFIX = "bernstein.adapter:"

#: Bumped only when the derivation recipe itself changes. Recorded in the
#: replay index so a stale index produced by an older recipe is detectable.
DERIVE_RECIPE_VERSION = 1

#: Name of the per-``.sdd`` index file mapping ``(conversation_id, adapter)``
#: to a recorded run, so replay can resolve a prior run without scanning logs.
SESSION_INDEX_FILENAME = "session_index.json"


def _normalise(value: str, *, label: str) -> str:
    """Validate and return a non-empty derivation input."""
    # Guard against non-str callers at runtime even though the annotation
    # promises a str; cast to ``object`` so the strict type checker does not
    # flag the isinstance as unnecessary.
    if not isinstance(cast("object", value), str):
        raise TypeError(f"{label} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def derive_session_id(conversation_id: str, adapter_name: str) -> UUID:
    """Derive a deterministic session id for one adapter conversation.

    The result is a stable :class:`uuid.UUID`: identical inputs always
    produce the same id across processes and runs, and the id differs when
    either ``conversation_id`` or ``adapter_name`` differs.

    Args:
        conversation_id: The orchestrator's conversation id. Used as the
            HMAC key so two conversations never share a derived id.
        adapter_name: The registry name of the adapter (for example
            ``"codex"``). Appended to :data:`NAMESPACE_PREFIX` so distinct
            adapters occupy disjoint id spaces.

    Returns:
        A version-5-shaped :class:`uuid.UUID` derived from the inputs.

    Raises:
        ValueError: If either input is empty.
        TypeError: If either input is not a string.
    """
    conversation_id = _normalise(conversation_id, label="conversation_id")
    adapter_name = _normalise(adapter_name, label="adapter_name")

    message = (NAMESPACE_PREFIX + adapter_name).encode("utf-8")
    digest = hmac.new(conversation_id.encode("utf-8"), message, hashlib.sha256).digest()

    # Take the first 16 bytes and stamp RFC 4122 version (5) + variant bits,
    # mirroring how uuid5 shapes a SHA-1 digest into a UUID.
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50  # version 5
    raw[8] = (raw[8] & 0x3F) | 0x80  # variant RFC 4122
    return UUID(bytes=bytes(raw))


@dataclass(frozen=True)
class SessionIdRecord:
    """One entry in the replay session index."""

    conversation_id: str
    adapter_name: str
    session_id: str
    run_id: str
    recipe_version: int = DERIVE_RECIPE_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "adapter_name": self.adapter_name,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "recipe_version": self.recipe_version,
        }


def _index_key(conversation_id: str, adapter_name: str) -> str:
    """Stable composite key for the on-disk index map."""
    # JSON object keys must be strings; encode the pair so it round-trips
    # without ambiguity even if a name contains the separator.
    return json.dumps([conversation_id, adapter_name], separators=(",", ":"))


class SessionIdIndex:
    """File-backed map from ``(conversation_id, adapter_name)`` to a run.

    The replay subsystem records each run's derived session id here so a
    later replay can resolve the prior run directly, instead of scanning
    ``events.jsonl`` files. The index lives at
    ``<sdd_dir>/<SESSION_INDEX_FILENAME>`` and is written atomically.
    """

    def __init__(self, sdd_dir: Path) -> None:
        self._path = sdd_dir / SESSION_INDEX_FILENAME
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """Path to the backing index file."""
        return self._path

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {}
        try:
            raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, object]] = {}
        for key, value in raw.items():  # type: ignore[misc]  # json values are object
            if isinstance(value, dict):
                out[str(key)] = {str(k): v for k, v in value.items()}  # type: ignore[misc]
        return out

    def record(self, conversation_id: str, adapter_name: str, run_id: str) -> SessionIdRecord:
        """Bind ``(conversation_id, adapter_name)`` to ``run_id``.

        Derives the session id, writes the mapping, and returns the record.
        The most recent binding for a key wins, so a rerun overwrites the
        prior slot rather than appending a duplicate.
        """
        conversation_id = _normalise(conversation_id, label="conversation_id")
        adapter_name = _normalise(adapter_name, label="adapter_name")
        run_id = _normalise(run_id, label="run_id")

        record = SessionIdRecord(
            conversation_id=conversation_id,
            adapter_name=adapter_name,
            session_id=str(derive_session_id(conversation_id, adapter_name)),
            run_id=run_id,
        )
        with self._lock:
            data = self._load()
            data[_index_key(conversation_id, adapter_name)] = record.to_dict()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)
        return record

    def lookup(self, conversation_id: str, adapter_name: str) -> SessionIdRecord | None:
        """Return the recorded run for a key, or ``None`` if not indexed.

        This is the AC #4 entry point: locate a previous run by
        ``(conversation_id, adapter_name)`` without scanning logs.
        """
        with self._lock:
            data = self._load()
        entry = data.get(_index_key(conversation_id, adapter_name))
        if entry is None:
            return None
        try:
            return SessionIdRecord(
                conversation_id=str(entry["conversation_id"]),
                adapter_name=str(entry["adapter_name"]),
                session_id=str(entry["session_id"]),
                run_id=str(entry["run_id"]),
                recipe_version=int(str(entry.get("recipe_version", DERIVE_RECIPE_VERSION))),
            )
        except (KeyError, TypeError, ValueError):
            return None
