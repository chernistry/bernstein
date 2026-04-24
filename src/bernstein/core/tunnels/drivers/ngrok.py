"""ngrok driver.

Runs ``ngrok http <port> --log stdout --log-format json`` and parses
the first ``url=https://...`` field out of the JSON log stream.
"""

from __future__ import annotations

import json

from bernstein.core.tunnels.drivers._base import _StreamProcessDriver


class NgrokDriver(_StreamProcessDriver):
    """Driver wrapping the ``ngrok`` binary."""

    name = "ngrok"
    binary = "ngrok"
    install_hint = "brew install ngrok/ngrok/ngrok"
    no_url_error = "ngrok did not emit a public URL in time"

    def build_argv(self, port: int) -> list[str]:
        """Return the argv for ``ngrok http <port>`` with JSON logs."""
        return [
            self.binary,
            "http",
            str(port),
            "--log",
            "stdout",
            "--log-format",
            "json",
        ]

    @staticmethod
    def parse_url(output: str) -> str | None:
        """Pull the first public ``https://`` URL from ngrok's JSON log.

        ngrok writes one JSON object per line; the tunnel-started event
        includes a ``url`` key with the public URL.
        """
        for raw in output.splitlines():
            line = raw.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            url = obj.get("url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
        return None
