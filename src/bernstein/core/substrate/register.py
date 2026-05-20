"""Idempotent, backup-first registration of Bernstein into a host config.

The write contract:

* Merge a single ``bernstein`` entry into the host's server map; never
  touch unrelated keys (acceptance criterion: never clobber existing
  config).
* Back up the existing config file before the first mutating write.
* Be idempotent: re-registering an already-correct entry reports
  ``already_registered`` and performs no write (and creates no backup).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from bernstein.core.substrate.host_registry import (
    SERVER_ID,
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


def _load_config(path: Path) -> dict[str, Any]:
    """Read an existing JSON host config, tolerating absence/garbage.

    A missing or unparseable file yields an empty dict so registration can
    bootstrap a fresh config without discarding a valid one.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"existing config at {path} is not valid JSON; refusing to overwrite") from exc
    if not isinstance(data, dict):
        raise ValueError(f"existing config at {path} is not a JSON object; refusing to overwrite")
    return cast("dict[str, Any]", data)


def _backup(path: Path, *, now: datetime | None = None) -> Path:
    """Copy ``path`` to a timestamped ``.bak`` sibling and return its path."""
    stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def is_registered(host: HostSpec, *, path: Path | None = None) -> bool:
    """Return True when the host config already has the Bernstein entry."""
    target = path or host.config_path()
    if target is None:
        return False
    config = _load_config(target)
    servers = config.get(host.config_key)
    if not isinstance(servers, dict):
        return False
    return SERVER_ID in servers


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
            the existing config file is not a JSON object.
    """
    if not host.supported:
        raise ValueError(f"host {host.name!r} is not yet supported for registration")

    target = path or host.config_path()
    if target is None:
        raise ValueError(f"host {host.name!r} has no resolvable config path on this OS")

    config = _load_config(target)
    raw_servers = config.get(host.config_key)
    servers: dict[str, Any] = dict(cast("dict[str, Any]", raw_servers)) if isinstance(raw_servers, dict) else {}

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
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)

    return RegisterResult(host.name, target, action, backup_path)


__all__ = [
    "RegisterResult",
    "is_registered",
    "register_host",
]
