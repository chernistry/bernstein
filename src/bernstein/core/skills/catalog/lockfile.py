"""``skills.lock`` extensions for catalog installs and cross-worktree consistency.

Layout
------

The existing :mod:`bernstein.core.skills.lifecycle` lockfile records
locally-sourced skills. Catalog installs extend the same physical file
with an additional ``[[catalog]]`` array of TOML tables::

    [[catalog]]
    id = "code-review"
    name = "code-review"
    version = "1.2.0"
    manifest_url = "github://acme/code-review-skill@v1.2.0"
    manifest_sha256 = "<hex>"
    content_digest = "<hex>"
    install_id = "<uuid>"
    chain_head = "<hex>"
    installed_at = "<isoformat>"

A second ``[[lineage_receipt]]`` array records every chain-head change
so a sibling worktree can decide deterministically whether to adopt the
upstream update or pin to the previous head.

Atomicity
---------

The lockfile is written to a sibling ``.tmp`` file and then
:func:`pathlib.Path.replace` swaps it into place, which is atomic on
POSIX and on Windows (since 3.3). Parallel worktrees launched from the
same chain head therefore observe identical post-write state.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from bernstein.core.skills.lifecycle import (
    SKILLS_LOCK_FILENAME,
    LockEntry,
    _read_lock,
    _toml_quote,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

#: Filename of the catalog-extended lock; same as the lifecycle lock so
#: legacy callers continue to see both arrays in the same file.
CATALOG_LOCK_FILENAME = SKILLS_LOCK_FILENAME

#: Receipt event for adopting an upgrade.
RECEIPT_ADOPT = "adopt"

#: Receipt event for pinning to the prior chain head.
RECEIPT_PIN = "pin"

#: Receipt event for first install (no prior head to pin against).
RECEIPT_INSTALL = "install"


@dataclass(frozen=True)
class CatalogLockEntry:
    """One ``[[catalog]]`` row in ``skills.lock``."""

    id: str
    name: str
    version: str
    manifest_url: str
    manifest_sha256: str
    content_digest: str
    install_id: str
    chain_head: str
    installed_at: str


@dataclass(frozen=True)
class LineageReceipt:
    """One ``[[lineage_receipt]]`` row in ``skills.lock``.

    Attributes:
        worktree_id: Stable identifier of the worktree that emitted the
            receipt; defaults to the worktree path's SHA-256.
        action: One of :data:`RECEIPT_INSTALL`, :data:`RECEIPT_ADOPT`,
            :data:`RECEIPT_PIN`.
        entry_id: The catalog entry this receipt covers.
        from_chain_head: Chain head prior to the action (genesis "0"*64
            on first install).
        to_chain_head: Chain head after the action.
        manifest_sha256: SHA of the manifest that triggered the receipt.
        timestamp: ISO-8601 UTC string.
    """

    worktree_id: str
    action: str
    entry_id: str
    from_chain_head: str
    to_chain_head: str
    manifest_sha256: str
    timestamp: str


@dataclass(frozen=True)
class CatalogLockState:
    """Parsed view of the catalog-extended ``skills.lock``."""

    local: list[LockEntry] = field(default_factory=list)
    catalog: list[CatalogLockEntry] = field(default_factory=list)
    receipts: list[LineageReceipt] = field(default_factory=list)

    def find_catalog(self, entry_id: str) -> CatalogLockEntry | None:
        """Return the lock row for ``entry_id`` or ``None``."""
        for row in self.catalog:
            if row.id == entry_id:
                return row
        return None

    def digest(self) -> str:
        """Return a deterministic digest of the catalog portion only.

        Two worktrees on the same chain head should produce identical
        digests; this is the value the CI lineage gate inspects.
        """
        canonical = json.dumps(
            [
                {
                    "id": row.id,
                    "version": row.version,
                    "manifest_sha256": row.manifest_sha256,
                    "content_digest": row.content_digest,
                    "chain_head": row.chain_head,
                }
                for row in sorted(self.catalog, key=lambda row: row.id)
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def worktree_id_for(workdir: Path) -> str:
    """Return a stable worktree identifier (SHA-256 of resolved path)."""
    return hashlib.sha256(str(workdir.resolve()).encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


def _read_catalog_state(path: Path) -> CatalogLockState:
    """Parse the lockfile and return both local and catalog views.

    Falls back to an empty state on any parse error so subsequent writes
    can self-heal the file.
    """
    if not path.is_file():
        return CatalogLockState()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return CatalogLockState()

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return CatalogLockState()

    # Reuse the lifecycle reader for the [[skills]] array.
    local = list(_read_lock(path).values())

    catalog_rows: list[CatalogLockEntry] = []
    for raw in cast("list[object]", data.get("catalog", [])):
        if not isinstance(raw, dict):
            continue
        row = cast("dict[str, object]", raw)
        try:
            entry = CatalogLockEntry(
                id=_required_str(row, "id"),
                name=_required_str(row, "name"),
                version=_required_str(row, "version"),
                manifest_url=_required_str(row, "manifest_url"),
                manifest_sha256=_required_str(row, "manifest_sha256"),
                content_digest=_required_str(row, "content_digest"),
                install_id=_required_str(row, "install_id"),
                chain_head=_required_str(row, "chain_head"),
                installed_at=_required_str(row, "installed_at"),
            )
        except _MissingField:
            continue
        catalog_rows.append(entry)

    receipts: list[LineageReceipt] = []
    for raw in cast("list[object]", data.get("lineage_receipt", [])):
        if not isinstance(raw, dict):
            continue
        row = cast("dict[str, object]", raw)
        try:
            receipt = LineageReceipt(
                worktree_id=_required_str(row, "worktree_id"),
                action=_required_str(row, "action"),
                entry_id=_required_str(row, "entry_id"),
                from_chain_head=_required_str(row, "from_chain_head"),
                to_chain_head=_required_str(row, "to_chain_head"),
                manifest_sha256=_required_str(row, "manifest_sha256"),
                timestamp=_required_str(row, "timestamp"),
            )
        except _MissingField:
            continue
        receipts.append(receipt)

    return CatalogLockState(local=local, catalog=catalog_rows, receipts=receipts)


class _MissingField(Exception):
    """Internal sentinel for malformed lockfile rows."""


def _required_str(row: dict[str, object], key: str) -> str:
    """Return ``row[key]`` as a string or raise :class:`_MissingField`."""
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise _MissingField(key)
    return value


def _write_state(path: Path, state: CatalogLockState) -> None:
    """Write the merged lockfile to disk atomically.

    The lifecycle :func:`bernstein.core.skills.lifecycle._write_lock`
    overwrites the entire file with only the local ``[[skills]]`` rows;
    we replicate its determinism (sorted keys, fixed quoting, blank line
    between tables) while emitting both arrays so the file remains a
    single source of truth.
    """
    lines: list[str] = [
        "# bernstein skills lock file - regenerated by `bernstein skills` commands.",
        "# Do not edit by hand.",
        "",
    ]
    for entry in sorted(state.local, key=lambda row: row.name):
        lines.extend(
            (
                "[[skills]]",
                f"name = {_toml_quote(entry.name)}",
                f"source = {_toml_quote(entry.source)}",
                f"path = {_toml_quote(entry.path)}",
                f"digest = {_toml_quote(entry.digest)}",
                "",
            )
        )
    for row in sorted(state.catalog, key=lambda row: row.id):
        lines.extend(
            (
                "[[catalog]]",
                f"id = {_toml_quote(row.id)}",
                f"name = {_toml_quote(row.name)}",
                f"version = {_toml_quote(row.version)}",
                f"manifest_url = {_toml_quote(row.manifest_url)}",
                f"manifest_sha256 = {_toml_quote(row.manifest_sha256)}",
                f"content_digest = {_toml_quote(row.content_digest)}",
                f"install_id = {_toml_quote(row.install_id)}",
                f"chain_head = {_toml_quote(row.chain_head)}",
                f"installed_at = {_toml_quote(row.installed_at)}",
                "",
            )
        )
    for receipt in sorted(
        state.receipts,
        key=lambda r: (r.timestamp, r.entry_id, r.worktree_id),
    ):
        lines.extend(
            (
                "[[lineage_receipt]]",
                f"worktree_id = {_toml_quote(receipt.worktree_id)}",
                f"action = {_toml_quote(receipt.action)}",
                f"entry_id = {_toml_quote(receipt.entry_id)}",
                f"from_chain_head = {_toml_quote(receipt.from_chain_head)}",
                f"to_chain_head = {_toml_quote(receipt.to_chain_head)}",
                f"manifest_sha256 = {_toml_quote(receipt.manifest_sha256)}",
                f"timestamp = {_toml_quote(receipt.timestamp)}",
                "",
            )
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    # ``Path.replace`` is atomic on POSIX and on Windows (since 3.3).
    tmp.replace(path)


def _acquire_lock(lock_path: Path, *, timeout: float = 10.0) -> Path:
    """Acquire a coarse file-lock sibling to the lockfile.

    Used by parallel worktrees that share a project root via a shared
    network filesystem. The lock is best-effort; on local filesystems the
    atomic ``replace`` already provides the durability guarantee.
    """
    lock_file = lock_path.with_suffix(lock_path.suffix + ".guard")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() > deadline:
                # Stale guard, force takeover; better than wedging.
                lock_file.unlink(missing_ok=True)
                continue
            time.sleep(0.05)
            continue
        try:
            with contextlib.suppress(OSError):
                os.write(fd, str(os.getpid()).encode())
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
        return lock_file


def _release_lock(lock_file: Path) -> None:
    """Release the coarse file-lock."""
    lock_file.unlink(missing_ok=True)


def upsert_catalog_install(
    lockfile_path: Path,
    entry: CatalogLockEntry,
    *,
    workdir: Path,
    from_chain_head: str = "0" * 64,
) -> CatalogLockState:
    """Insert or replace a catalog row, emitting a lineage receipt.

    Args:
        lockfile_path: Path to ``skills.lock``.
        entry: The row to write.
        workdir: Worktree root, used to derive the receipt's
            ``worktree_id``.
        from_chain_head: Chain head visible to this worktree before the
            install. The very first install passes the genesis digest.

    Returns:
        The post-write :class:`CatalogLockState`.
    """
    guard = _acquire_lock(lockfile_path)
    try:
        state = _read_catalog_state(lockfile_path)
        catalog = [row for row in state.catalog if row.id != entry.id]
        prior = next((row for row in state.catalog if row.id == entry.id), None)
        catalog.append(entry)

        action: str
        if prior is None:
            action = RECEIPT_INSTALL
            from_head = from_chain_head
        elif prior.chain_head == entry.chain_head:
            # No-op rewrite (e.g. content_digest stable); still record a
            # pin-style receipt so the cross-worktree decision is
            # explicit and deterministic.
            action = RECEIPT_PIN
            from_head = prior.chain_head
        else:
            action = RECEIPT_ADOPT
            from_head = prior.chain_head

        receipt = LineageReceipt(
            worktree_id=worktree_id_for(workdir),
            action=action,
            entry_id=entry.id,
            from_chain_head=from_head,
            to_chain_head=entry.chain_head,
            manifest_sha256=entry.manifest_sha256,
            timestamp=_utc_now(),
        )
        receipts = [*state.receipts, receipt]

        new_state = CatalogLockState(local=state.local, catalog=catalog, receipts=receipts)
        _write_state(lockfile_path, new_state)
        return new_state
    finally:
        _release_lock(guard)


def record_pin(
    lockfile_path: Path,
    *,
    entry_id: str,
    chain_head: str,
    manifest_sha256: str,
    workdir: Path,
) -> CatalogLockState:
    """Record a deterministic pin receipt without changing the install.

    Used by the sibling worktree when it decides to stay on the prior
    install despite seeing a fresh chain head in the audit log.
    """
    guard = _acquire_lock(lockfile_path)
    try:
        state = _read_catalog_state(lockfile_path)
        receipt = LineageReceipt(
            worktree_id=worktree_id_for(workdir),
            action=RECEIPT_PIN,
            entry_id=entry_id,
            from_chain_head=chain_head,
            to_chain_head=chain_head,
            manifest_sha256=manifest_sha256,
            timestamp=_utc_now(),
        )
        new_state = CatalogLockState(
            local=state.local,
            catalog=state.catalog,
            receipts=[*state.receipts, receipt],
        )
        _write_state(lockfile_path, new_state)
        return new_state
    finally:
        _release_lock(guard)


def remove_catalog_entry(
    lockfile_path: Path,
    entry_id: str,
) -> CatalogLockState:
    """Remove a catalog row from the lockfile.

    Used by ``bernstein skills catalog uninstall``. The lockfile is
    rewritten atomically so concurrent readers either see the row or
    don't.
    """
    guard = _acquire_lock(lockfile_path)
    try:
        state = _read_catalog_state(lockfile_path)
        new_state = CatalogLockState(
            local=state.local,
            catalog=[row for row in state.catalog if row.id != entry_id],
            receipts=state.receipts,
        )
        _write_state(lockfile_path, new_state)
        return new_state
    finally:
        _release_lock(guard)


def detect_drift(
    lockfile_path: Path,
    installed_digests: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """Compare lockfile content_digest with what's actually installed.

    Args:
        lockfile_path: Path to ``skills.lock``.
        installed_digests: Map of ``entry.id -> on-disk content_digest``.

    Returns:
        Map of ``entry.id -> (locked_digest, installed_digest)`` for every
        entry whose installed digest disagrees with the lockfile.
    """
    state = _read_catalog_state(lockfile_path)
    out: dict[str, tuple[str, str]] = {}
    for row in state.catalog:
        actual = installed_digests.get(row.id)
        if actual is None:
            out[row.id] = (row.content_digest, "<missing>")
            continue
        if actual != row.content_digest:
            out[row.id] = (row.content_digest, actual)
    return out


def read_state(lockfile_path: Path) -> CatalogLockState:
    """Public reader for tests and CLI commands."""
    return _read_catalog_state(lockfile_path)


def write_state(lockfile_path: Path, state: CatalogLockState) -> None:
    """Public writer for tests; production callers should prefer the
    higher-level helpers above so receipts are recorded correctly."""
    _write_state(lockfile_path, state)


def fresh_install_id() -> str:
    """Return a new install identifier; thin wrapper for tests."""
    return uuid.uuid4().hex


__all__ = [
    "CATALOG_LOCK_FILENAME",
    "RECEIPT_ADOPT",
    "RECEIPT_INSTALL",
    "RECEIPT_PIN",
    "CatalogLockEntry",
    "CatalogLockState",
    "LineageReceipt",
    "detect_drift",
    "fresh_install_id",
    "read_state",
    "record_pin",
    "remove_catalog_entry",
    "upsert_catalog_install",
    "worktree_id_for",
    "write_state",
]
