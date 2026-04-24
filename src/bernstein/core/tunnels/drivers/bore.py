"""bore.pub driver.

Runs ``bore local <port> --to bore.pub`` and parses ``listening at
bore.pub:<PORT>`` from stdout to build the public URL.
"""

from __future__ import annotations

import re

from bernstein.core.tunnels.drivers._base import _StreamProcessDriver

_LISTEN_RE = re.compile(r"listening at\s+(?P<host>[a-zA-Z0-9.-]+):(?P<port>\d+)")


class BoreDriver(_StreamProcessDriver):
    """Driver wrapping the ``bore`` binary against ``bore.pub``."""

    name = "bore"
    binary = "bore"
    install_hint = "cargo install bore-cli"
    no_url_error = "bore did not announce a remote port in time"

    def build_argv(self, port: int) -> list[str]:
        """Return the argv for ``bore local <port> --to bore.pub``."""
        return [self.binary, "local", str(port), "--to", "bore.pub"]

    @staticmethod
    def parse_url(output: str) -> str | None:
        """Build the public ``tcp://`` URL from a bore ``listening at`` line."""
        match = _LISTEN_RE.search(output)
        if match is None:
            return None
        return f"tcp://{match.group('host')}:{match.group('port')}"
