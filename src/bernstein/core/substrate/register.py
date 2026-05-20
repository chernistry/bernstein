"""Idempotent, backup-first registration of Bernstein into a host config.

The write contract:

* Merge a single ``bernstein`` entry into the host's server map; never
  touch unrelated keys (acceptance criterion: never clobber existing
  config).
* Back up the existing config file before the first mutating write.
* Be idempotent: re-registering an already-correct entry reports
  ``already_registered`` and performs no write (and creates no backup).

Both JSON hosts (Claude Desktop, Claude Code, Cursor, Continue, Cline,
Zed) and YAML hosts (Aider) share the same merge contract; the dispatch
on :class:`ConfigFormat` keeps the per-host adapter surface declarative.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from bernstein.core.substrate.host_registry import (
    SERVER_ID,
    ConfigFormat,
    HostSpec,
    bernstein_server_entry,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class RegisterResult:
    """Outcome of a registration attempt.

    Attributes:
        host: Host identifier.
        config_path: Path that was (or would be) written.
        action: ``registered`` | ``updated`` | ``already_registered``.
        backup_path: Path of the backup taken, or ``None`` when no write
            occurred or the config file did not previously exist.
    """

    host: str
    config_path: Path
    action: str
    backup_path: Path | None


# ---------------------------------------------------------------------------
# Format-aware load / dump
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"existing config at {path} is not readable; refusing to overwrite") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"existing config at {path} is not valid JSON; refusing to overwrite") from exc
    if not isinstance(data, dict):
        raise ValueError(f"existing config at {path} is not a JSON object; refusing to overwrite")
    return cast("dict[str, Any]", data)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"existing config at {path} is not readable; refusing to overwrite") from exc
    if not text.strip():
        return {}
    try:
        import yaml  # local import keeps the optional surface honest
    except ImportError as exc:  # pragma: no cover -- yaml is a runtime dep
        raise ValueError("PyYAML is required to register YAML-based hosts") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"existing config at {path} is not valid YAML; refusing to overwrite") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"existing config at {path} is not a YAML mapping; refusing to overwrite")
    return cast("dict[str, Any]", data)


def _load_config(path: Path, fmt: ConfigFormat) -> dict[str, Any]:
    """Read an existing host config, tolerating absence.

    A missing file yields an empty dict so registration can bootstrap a
    fresh config. An unreadable or invalid file raises ``ValueError`` so a
    mutating write never clobbers existing-but-unreadable state.
    """
    if not path.exists():
        return {}
    if fmt is ConfigFormat.YAML:
        return _load_yaml(path)
    return _load_json(path)


def _dump_config(data: dict[str, Any], fmt: ConfigFormat) -> str:
    """Serialise ``data`` for the given on-disk format.

    JSON is pretty-printed with a trailing newline (matching Claude
    Desktop / Cursor conventions). YAML is dumped with block style and
    sort-keys off to keep operator-edited keys in their original order.
    """
    if fmt is ConfigFormat.YAML:
        import yaml

        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    return json.dumps(data, indent=2) + "\n"


def _backup(path: Path, *, now: datetime | None = None) -> Path:
    """Copy ``path`` to a timestamped ``.bak`` sibling and return its path."""
    stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%d%H%M%S%f")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def is_registered(host: HostSpec, *, path: Path | None = None) -> bool:
    """Return True when the host config already has the Bernstein entry."""
    target = path or host.config_path()
    if target is None:
        return False
    try:
        config = _load_config(target, host.config_format)
    except ValueError:
        # Unreadable / invalid configs are not registered for our purposes.
        return False
    servers = config.get(host.config_key)
    if not isinstance(servers, dict):
        return False
    return SERVER_ID in servers


def is_stale(host: HostSpec, *, path: Path | None = None) -> bool:
    """Return True when an existing entry differs from the canonical entry.

    A host is stale when it has a ``bernstein`` entry but the recorded
    command/args do not match :func:`bernstein_server_entry`. The
    ``doctor --substrate`` reporter surfaces this so operators can re-run
    ``desktop-register`` after upgrading Python.
    """
    target = path or host.config_path()
    if target is None:
        return False
    try:
        config = _load_config(target, host.config_format)
    except ValueError:
        return False
    servers = config.get(host.config_key)
    if not isinstance(servers, dict):
        return False
    entry = servers.get(SERVER_ID)
    if entry is None:
        return False
    return entry != bernstein_server_entry()


def register_host(
    host: HostSpec,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> RegisterResult:
    """Merge Bernstein's MCP entry into ``host``'s config, idempotently.

    Args:
        host: Target host spec (must be supported).
        path: Override the resolved config path (testing).
        now: Override the wall clock for backup naming (testing).

    Returns:
        A :class:`RegisterResult` describing the action taken.

    Raises:
        ValueError: When the host is stubbed, has no resolvable path, or
            the existing config file is not a parseable mapping.
    """
    if not host.supported:
        raise ValueError(f"host {host.name!r} is not yet supported for registration")

    target = path or host.config_path()
    if target is None:
        raise ValueError(f"host {host.name!r} has no resolvable config path on this OS")

    config = _load_config(target, host.config_format)
    raw_servers = config.get(host.config_key)
    servers: dict[str, Any] = cast("dict[str, Any]", raw_servers).copy() if isinstance(raw_servers, dict) else {}

    desired = bernstein_server_entry()
    existing_entry: Any = servers.get(SERVER_ID)

    if existing_entry == desired:
        return RegisterResult(host.name, target, "already_registered", None)

    action = "updated" if SERVER_ID in servers else "registered"

    backup_path: Path | None = None
    if target.exists():
        backup_path = _backup(target, now=now)

    servers[SERVER_ID] = desired
    config[host.config_key] = servers

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_dump_config(config, host.config_format), encoding="utf-8")
    tmp.replace(target)

    return RegisterResult(host.name, target, action, backup_path)


__all__ = [
    "RegisterResult",
    "is_registered",
    "is_stale",
    "register_host",
]
