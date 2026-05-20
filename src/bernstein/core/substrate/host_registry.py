"""Registry of host applications that can auto-discover Bernstein's MCP server.

Each :class:`HostSpec` records how to locate a host's MCP config file per
OS and whether registration is implemented yet. Two hosts are fully
supported (``claude-desktop``, ``claude-code``); the rest are stubbed so
the surface is discoverable without claiming behaviour that does not exist.

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

#: Key under ``mcpServers`` that Bernstein owns in any host config.
SERVER_ID = "bernstein"


class HostStatus(StrEnum):
    """Whether ``desktop-register`` can write a host's config today."""

    SUPPORTED = "supported"
    STUBBED = "stubbed"


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


# ---------------------------------------------------------------------------
# Host specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostSpec:
    """Describe one host application and where its MCP config lives.

    Attributes:
        name: Stable CLI identifier (e.g. ``claude-desktop``).
        display_name: Human-friendly name for tables and docs.
        status: Whether registration is implemented or stubbed.
        config_key: Top-level key holding the server map in the host config.
        scope: ``user`` (global config in home dir) or ``project`` (cwd).
        notes: One-line operator note (restart hint or stub reason).
        _path_resolver: Internal callable returning the config path.
    """

    name: str
    display_name: str
    status: HostStatus
    config_key: str = "mcpServers"
    scope: str = "user"
    notes: str = ""
    _path_resolver: object = field(default=None, repr=False, compare=False)

    @property
    def supported(self) -> bool:
        """True when ``desktop-register`` can write this host today."""
        return self.status is HostStatus.SUPPORTED

    def config_path(self) -> Path | None:
        """Resolve the host's MCP config path for the current OS.

        Returns ``None`` for stubbed hosts whose path is not yet wired up.
        """
        resolver = self._path_resolver
        if resolver is None:
            return None
        return resolver()  # type: ignore[operator]


def bernstein_server_entry() -> dict[str, object]:
    """Return the canonical ``mcpServers`` entry that launches Bernstein.

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


def _stub(name: str, display: str, path_resolver: object, notes: str, scope: str = "user") -> HostSpec:
    return HostSpec(
        name=name,
        display_name=display,
        status=HostStatus.STUBBED,
        scope=scope,
        notes=notes,
        _path_resolver=path_resolver,
    )


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
    "cursor": _stub(
        "cursor",
        "Cursor",
        lambda: _home() / ".cursor" / "mcp.json",
        "Not yet implemented; would merge into ~/.cursor/mcp.json.",
    ),
    "continue": _stub(
        "continue",
        "Continue",
        lambda: _home() / ".continue" / "config.json",
        "Not yet implemented; Continue uses its own mcpServers schema.",
    ),
    "cline": _stub(
        "cline",
        "Cline",
        lambda: _home() / ".cline" / "mcp_settings.json",
        "Not yet implemented; VS Code extension settings vary by install.",
    ),
    "zed": _stub(
        "zed",
        "Zed",
        lambda: _xdg_config_home() / "zed" / "settings.json",
        "Not yet implemented; Zed nests servers under context_servers.",
        scope="user",
    ),
    "aider": _stub(
        "aider",
        "Aider",
        lambda: _home() / ".aider.conf.yml",
        "Not yet implemented; Aider config is YAML, not mcpServers JSON.",
    ),
    "codex": _stub(
        "codex",
        "Codex",
        lambda: _home() / ".codex" / "config.toml",
        "Not yet implemented; Codex config is TOML.",
    ),
    "gemini": _stub(
        "gemini",
        "Gemini CLI",
        lambda: _home() / ".gemini" / "settings.json",
        "Not yet implemented; would merge into ~/.gemini/settings.json.",
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
    "HostSpec",
    "HostStatus",
    "bernstein_server_entry",
    "get_host",
    "known_host_names",
]
