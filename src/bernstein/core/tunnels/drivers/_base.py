"""Shared base class for tunnel drivers that follow the "spawn a binary
and scrape the public URL from its stdout" pattern (cloudflared, bore,
ngrok).

Subclasses override:

* ``name`` / ``binary`` / ``install_hint`` class attributes,
* :meth:`build_argv` — the command line,
* :meth:`parse_url` — extracts the public URL from accumulated stdout,
* :attr:`no_url_error` — the message when the binary doesn't emit a URL
  inside :attr:`_START_TIMEOUT_S`.

Everything else — detect, process accounting, SIGTERM teardown — lives
here. Keeps each concrete driver to ~40 lines.
"""

from __future__ import annotations

import contextlib
import shutil
import signal
import subprocess
import time
from abc import abstractmethod

from bernstein.core.tunnels.protocol import (
    Detected,
    ProviderNotAvailable,
    TunnelHandle,
    TunnelProvider,
)

_START_TIMEOUT_S = 30.0


class _StreamProcessDriver(TunnelProvider):
    """Tunnel driver for binaries that print a public URL on stdout."""

    install_hint: str = ""
    no_url_error: str = "tunnel binary did not emit a public URL in time"

    def __init__(self) -> None:
        """Initialize the driver with an empty process table."""
        self._procs: dict[str, subprocess.Popen[str]] = {}

    # ------------------------------------------------------------------
    # Contract hooks — subclasses must implement.
    # ------------------------------------------------------------------

    @abstractmethod
    def build_argv(self, port: int) -> list[str]:
        """Return the subprocess argv that exposes ``port`` publicly."""

    @staticmethod
    @abstractmethod
    def parse_url(output: str) -> str | None:
        """Extract the public URL from accumulated stdout, if present."""

    # ------------------------------------------------------------------
    # Shared behaviour.
    # ------------------------------------------------------------------

    def detect(self) -> Detected:
        """Probe for the driver's binary with ``<binary> --version``.

        Returns:
            A :class:`Detected` describing the binary.

        Raises:
            ProviderNotAvailable: If the binary is missing or unrunnable.
        """
        path = shutil.which(self.binary)
        if path is None:
            raise ProviderNotAvailable(
                f"{self.binary} is not installed.",
                hint=self.install_hint,
            )
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderNotAvailable(
                f"{self.binary} failed to run: {exc}",
                hint=self.install_hint,
            ) from exc
        output = res.stdout or res.stderr or ""
        version = output.strip().splitlines()[0] if output else ""
        return Detected(binary_path=path, version=version)

    def start(self, port: int, name: str) -> TunnelHandle:
        """Spawn the binary and wait for its public URL to appear.

        Args:
            port: Local TCP port to expose.
            name: Tunnel name used as the key in the process table.

        Returns:
            A :class:`TunnelHandle` once the public URL is observed.

        Raises:
            ProviderNotAvailable: If the binary is missing.
            RuntimeError: If no URL is emitted inside the startup window.
        """
        self.detect()
        proc = subprocess.Popen(
            self.build_argv(port),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._procs[name] = proc

        assert proc.stdout is not None
        deadline = time.monotonic() + _START_TIMEOUT_S
        buf: list[str] = []
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            buf.append(line)
            url = self.parse_url("".join(buf))
            if url:
                return TunnelHandle(
                    name=name,
                    provider=self.name,
                    port=port,
                    public_url=url,
                    pid=proc.pid,
                )
        self.stop(name)
        raise RuntimeError(self.no_url_error)

    def stop(self, name: str) -> None:
        """Terminate the process for ``name`` with SIGTERM, then SIGKILL.

        Args:
            name: Tunnel name previously returned by :meth:`start`.
        """
        proc = self._procs.pop(name, None)
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            with contextlib.suppress(OSError):
                proc.kill()
