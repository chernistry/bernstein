"""Registry of host applications that can auto-discover Bernstein's MCP server.

Each :class:`HostSpec` records how to locate a host's config file per OS,
which config format and key the host uses, and whether registration is
implemented. Most priority hosts are JSON-with-``mcpServers``; Zed nests
under ``context_servers`` and Aider uses YAML. The shape differences are
captured declaratively so :mod:`bernstein.core.substrate.register` can
stay format-agnostic.

Path resolution is intentionally pure (no filesystem writes here) so it is
trivially testable. Actual config merges live in
:mod:`bernstein.core.substrate.register`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Key under ``mcpServers`` that Bernstein owns in any host config.
SERVER_ID = "bernstein"


class HostStatus(StrEnum):
    """Whether ``desktop-register`` can write a host's config today."""

    SUPPORTED = "supported"
    STUBBED = "stubbed"


class ConfigFormat(StrEnum):
    """On-disk format of a host's config file.

    JSON covers Claude Desktop, Claude Code, Cursor, Continue, Cline, Zed.
    YAML covers Aider (whose ``.aider.conf.yml`` is YAML, not JSON).
    """

    JSON = "json"
    YAML = "yaml"


# ---------------------------------------------------------------------------
# Per-OS config path resolution
# ---------------------------------------------------------------------------


def _platform_key() -> str:
    """Return one of ``macos`` / ``linux`` / ``windows`` for the current OS."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _home() -> Path:
    """Resolve the user home directory (override via ``HOME`` for tests)."""
    return Path(os.environ.get("HOME") or Path.home())


def _xdg_config_home() -> Path:
    """Resolve ``$XDG_CONFIG_HOME`` with the documented fallback."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg).expanduser() if xdg else _home() / ".config"


def _windows_appdata() -> Path:
    """Resolve ``%APPDATA%`` with a sensible fallback."""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else _home() / "AppData" / "Roaming"


# Path templates per host, keyed by platform. Resolved lazily so an OS we do
# not run on never has its env vars dereferenced.


def _claude_desktop_path() -> Path:
    plat = _platform_key()
    if plat == "macos":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if plat == "windows":
        return _windows_appdata() / "Claude" / "claude_desktop_config.json"
    # Linux is community-supported by Claude Desktop; XDG location.
    return _xdg_config_home() / "Claude" / "claude_desktop_config.json"


def _claude_code_path() -> Path:
    # Claude Code reads a project-local ``.mcp.json`` from the working dir.
    return Path.cwd() / ".mcp.json"


def _cursor_path() -> Path:
    # Cursor reads a user-global ``~/.cursor/mcp.json`` (and a project-local
    # ``./.cursor/mcp.json``); we target the user-global path for parity
    # with how Claude Desktop is registered.
    return _home() / ".cursor" / "mcp.json"


def _continue_path() -> Path:
    # Continue reads ``~/.continue/config.json`` for legacy MCP server
    # declarations. Newer Continue releases also accept ``config.yaml``;
    # we target the JSON path because every shipped version still reads
    # it and the merge is identical to the other JSON hosts.
    return _home() / ".continue" / "config.json"


def _cline_path() -> Path:
    # Cline stores its MCP settings inside the VS Code extension global
    # storage on every OS. The default location depends on the editor
    # variant; we target the stable canonical name used by Cline's own
    # docs, with an OS-specific parent.
    plat = _platform_key()
    vscode_user = "Code" + os.sep + "User"
    rel = Path(vscode_user) / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    if plat == "macos":
        return _home() / "Library" / "Application Support" / rel
    if plat == "windows":
        return _windows_appdata() / rel
    return _xdg_config_home() / rel


def _zed_path() -> Path:
    # Zed reads ``~/.config/zed/settings.json`` on Linux and
    # ``~/.config/zed/settings.json`` (via ``$XDG_CONFIG_HOME``) on macOS
    # too; Windows support is community-driven and shares the path.
    return _xdg_config_home() / "zed" / "settings.json"


def _aider_path() -> Path:
    # Aider reads ``~/.aider.conf.yml`` for its YAML config. Aider does
    # not natively load MCP servers; we record the entry under an
    # ``mcp-servers`` key so a community wrapper or operator script can
    # consume it (see ``docs/substrate/aider.md``).
    return _home() / ".aider.conf.yml"


# ---------------------------------------------------------------------------
# Host specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostSpec:
    """Describe one host application and where its config lives.

    Attributes:
        name: Stable CLI identifier (e.g. ``claude-desktop``).
        display_name: Human-friendly name for tables and docs.
        status: Whether registration is implemented or stubbed.
        config_key: Top-level key holding the server map in the host
            config (``mcpServers`` for Claude/Cursor/Continue/Cline,
            ``context_servers`` for Zed, ``mcp-servers`` for Aider).
        config_format: ``json`` or ``yaml``.
        scope: ``user`` (global config in home dir) or ``project`` (cwd).
        notes: One-line operator note (restart hint or stub reason).
        _path_resolver: Internal callable returning the config path.
    """

    name: str
    display_name: str
    status: HostStatus
    config_key: str = "mcpServers"
    config_format: ConfigFormat = ConfigFormat.JSON
    scope: str = "user"
    notes: str = ""
    _path_resolver: Callable[[], Path] | None = field(default=None, repr=False, compare=False)

    @property
    def supported(self) -> bool:
        """True when ``desktop-register`` can write this host today."""
        return self.status is HostStatus.SUPPORTED

    def config_path(self) -> Path | None:
        """Resolve the host's config path for the current OS.

        Returns ``None`` for stubbed hosts whose path is not yet wired up.
        """
        resolver = self._path_resolver
        if resolver is None:
            return None
        return resolver()


def bernstein_server_entry() -> dict[str, object]:
    """Return the canonical entry that launches Bernstein.

    Mirrors the entry written by orchestration bootstrap so a manually
    registered host behaves identically to an auto-discovered project.
    """
    return {
        "command": sys.executable,
        "args": ["-m", "bernstein.mcp"],
    }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


HOST_REGISTRY: dict[str, HostSpec] = {
    "claude-desktop": HostSpec(
        name="claude-desktop",
        display_name="Claude Desktop",
        status=HostStatus.SUPPORTED,
        scope="user",
        notes="Restart Claude Desktop after registering.",
        _path_resolver=_claude_desktop_path,
    ),
    "claude-code": HostSpec(
        name="claude-code",
        display_name="Claude Code",
        status=HostStatus.SUPPORTED,
        scope="project",
        notes="Writes .mcp.json in the current directory; reopen the project.",
        _path_resolver=_claude_code_path,
    ),
    "cursor": HostSpec(
        name="cursor",
        display_name="Cursor",
        status=HostStatus.SUPPORTED,
        scope="user",
        notes="Restart Cursor or reload the MCP servers panel.",
        _path_resolver=_cursor_path,
    ),
    "continue": HostSpec(
        name="continue",
        display_name="Continue",
        status=HostStatus.SUPPORTED,
        scope="user",
        notes="Restart your editor so Continue reloads ~/.continue/config.json.",
        _path_resolver=_continue_path,
    ),
    "cline": HostSpec(
        name="cline",
        display_name="Cline",
        status=HostStatus.SUPPORTED,
        scope="user",
        notes="Reload the VS Code window so Cline picks up the new MCP server.",
        _path_resolver=_cline_path,
    ),
    "zed": HostSpec(
        name="zed",
        display_name="Zed",
        status=HostStatus.SUPPORTED,
        config_key="context_servers",
        scope="user",
        notes="Restart Zed after registering; servers nest under context_servers.",
        _path_resolver=_zed_path,
    ),
    "aider": HostSpec(
        name="aider",
        display_name="Aider",
        status=HostStatus.SUPPORTED,
        config_key="mcp-servers",
        config_format=ConfigFormat.YAML,
        scope="user",
        notes="Aider has no native MCP loader; the entry is recorded for community wrappers.",
        _path_resolver=_aider_path,
    ),
    "codex": HostSpec(
        name="codex",
        display_name="Codex",
        status=HostStatus.STUBBED,
        scope="user",
        notes="Not yet implemented; Codex config is TOML.",
        _path_resolver=lambda: _home() / ".codex" / "config.toml",
    ),
    "gemini": HostSpec(
        name="gemini",
        display_name="Gemini CLI",
        status=HostStatus.STUBBED,
        scope="user",
        notes="Not yet implemented; would merge into ~/.gemini/settings.json.",
        _path_resolver=lambda: _home() / ".gemini" / "settings.json",
    ),
}


def known_host_names() -> list[str]:
    """Return host identifiers in a stable, alphabetised order."""
    return sorted(HOST_REGISTRY)


def get_host(name: str) -> HostSpec:
    """Look up a host by name.

    Raises:
        KeyError: When ``name`` is not a known host. The message lists the
            valid identifiers so the caller can surface a helpful error.
    """
    try:
        return HOST_REGISTRY[name]
    except KeyError as exc:
        valid = ", ".join(known_host_names())
        raise KeyError(f"unknown host {name!r}; known hosts: {valid}") from exc


__all__ = [
    "HOST_REGISTRY",
    "SERVER_ID",
    "ConfigFormat",
    "HostSpec",
    "HostStatus",
    "bernstein_server_entry",
    "get_host",
    "known_host_names",
]
