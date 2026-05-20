"""Substrate: register Bernstein into host applications (MCP servers, etc.).

A "host" is an application an operator already runs (Claude Desktop,
Claude Code, Cursor, Continue, Cline, Zed, Aider, ...) that auto-discovers
MCP servers via its own config file. This package describes each host
(``host_registry``) and performs idempotent, backup-first registration
writes.

Bernstein is a guest in the host's config: registration merges a single
``bernstein`` entry into the host's server map (``mcpServers`` for most
hosts, ``context_servers`` for Zed, ``mcp-servers`` for Aider) and never
clobbers unrelated keys.
"""

from __future__ import annotations

from bernstein.core.substrate.host_registry import (
    HOST_REGISTRY,
    ConfigFormat,
    HostSpec,
    HostStatus,
    bernstein_server_entry,
    get_host,
    known_host_names,
)
from bernstein.core.substrate.register import (
    RegisterResult,
    is_registered,
    is_stale,
    register_host,
)

__all__ = [
    "HOST_REGISTRY",
    "ConfigFormat",
    "HostSpec",
    "HostStatus",
    "RegisterResult",
    "bernstein_server_entry",
    "get_host",
    "is_registered",
    "is_stale",
    "known_host_names",
    "register_host",
]
