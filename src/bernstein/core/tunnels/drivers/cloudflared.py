"""Cloudflared quick-tunnel driver.

Shells out to ``cloudflared tunnel --url http://localhost:<port>`` and
parses the ``https://*.trycloudflare.com`` URL from stdout/stderr.
"""

from __future__ import annotations

import re

from bernstein.core.tunnels.drivers._base import _StreamProcessDriver

_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


class CloudflaredDriver(_StreamProcessDriver):
    """Driver wrapping the ``cloudflared`` binary."""

    name = "cloudflared"
    binary = "cloudflared"
    install_hint = "brew install cloudflared"
    no_url_error = "cloudflared did not emit a public URL in time"

    def build_argv(self, port: int) -> list[str]:
        """Return the argv for a quick tunnel against ``port``."""
        return [self.binary, "tunnel", "--url", f"http://localhost:{port}"]

    @staticmethod
    def parse_url(output: str) -> str | None:
        """Parse the public trycloudflare URL from captured output."""
        match = _URL_RE.search(output)
        return match.group(0) if match else None
