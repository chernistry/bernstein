"""Bounded, fail-closed HTTP client for opt-in operator telemetry.

Design constraints:

* Default state is off.  ``Client.is_enabled`` resolves the precedence
  chain on every call; the client never caches the result across emits.
* No network traffic unless ``is_enabled`` is True.
* Every send completes in <= 3s.  On any error, the command must still
  finish normally - exceptions are caught and swallowed at this boundary.
* Every emitted event is also appended to the local queue file
  ``~/.bernstein/telemetry-queue.jsonl`` so operators can ``cat`` it.
* The local queue is rotated weekly.

The client is intentionally synchronous.  ``flush`` is a no-op kept for
shape parity with future async backends.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from bernstein.core.telemetry import config as cfg
from bernstein.core.telemetry import install_id as install_id_mod
from bernstein.core.telemetry.events import (
    EventEnvelope,
    EventPayload,
    TelemetryEvent,
    build_envelope,
    serialize_event,
)

_LOG = logging.getLogger(__name__)

DEFAULT_ENDPOINT: Final[str] = "https://telemetry.bernstein.run/v1/events"
ENDPOINT_ENV: Final[str] = "BERNSTEIN_TELEMETRY_ENDPOINT"
TIMEOUT_SECONDS: Final[float] = 3.0
FLUSH_DEADLINE_SECONDS: Final[float] = 5.0
QUEUE_ROTATION_DAYS: Final[int] = 7


def _resolve_endpoint(env: dict[str, str] | None = None) -> str:
    """Return the receiver URL.  Operators may override via env."""
    real_env = env if env is not None else dict(os.environ)
    return real_env.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT


def _maybe_rotate_queue(path: Path) -> None:
    """Rotate the queue file weekly.

    Rotation moves ``telemetry-queue.jsonl`` -> ``telemetry-queue.<iso>.jsonl``
    when the mtime is older than ``QUEUE_ROTATION_DAYS``.
    """
    if not path.exists():
        return
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return
    if datetime.now(tz=UTC) - mtime <= timedelta(days=QUEUE_ROTATION_DAYS):
        return
    archive = path.with_name(f"telemetry-queue.{mtime.date().isoformat()}.jsonl")
    # Best effort.  A failed rotation must not block emission.
    with contextlib.suppress(OSError):
        path.rename(archive)


def _append_local(line: str, home: Path | None) -> None:
    """Append a serialized event line to the local queue file."""
    path = cfg.queue_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    _maybe_rotate_queue(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write("\n")


def read_recent_events(
    days: int = 30,
    home: Path | None = None,
) -> Iterator[str]:
    """Yield lines from the local queue for the last ``days`` days.

    Used by ``bernstein telemetry export``.  Operators can inspect exactly
    what was emitted under their install id.  Returns an empty iterator if
    the queue file does not exist.
    """
    path = cfg.queue_path(home)
    if not path.exists():
        return iter(())
    cutoff = date.today() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    def _gen() -> Iterator[str]:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.rstrip("\n")
                if not stripped:
                    continue
                day_part = _extract_day(stripped)
                if day_part is None or day_part >= cutoff_iso:
                    yield stripped

    return _gen()


def _extract_day(line: str) -> str | None:
    """Return the YYYY-MM-DD prefix of the line's timestamp, if any.

    Accepts either compact JSON (``"timestamp":"...``) or pretty-printed
    JSON (``"timestamp": "...``) so manually-authored queues can be read.
    """
    for marker in ('"timestamp":"', '"timestamp": "'):
        idx = line.find(marker)
        if idx == -1:
            continue
        start = idx + len(marker)
        return line[start : start + 10]
    return None


class Client:
    """Opt-in telemetry client.  Default state is off."""

    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        home: Path | None = None,
        endpoint: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._env = env
        self._home = home
        self._endpoint = endpoint or _resolve_endpoint(env)
        self._http = http_client
        self._owns_http = http_client is None

    @property
    def endpoint(self) -> str:
        """Return the resolved receiver URL."""
        return self._endpoint

    def is_enabled(self) -> bool:
        """Re-resolve precedence; never cached across calls."""
        return cfg.is_enabled(env=self._env, home=self._home)

    def _get_http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=TIMEOUT_SECONDS)
        return self._http

    def emit(
        self,
        name: TelemetryEvent,
        payload: EventPayload,
    ) -> bool:
        """Emit a single event.

        Returns ``True`` if the event was at least appended to the local
        queue (network may still have failed; that is fine).  Returns
        ``False`` if telemetry is currently disabled and nothing happened.

        Exceptions are never propagated.  This boundary is explicitly
        fail-closed: if anything goes wrong, the caller continues.
        """
        try:
            if not self.is_enabled():
                return False
            try:
                install_id = install_id_mod.ensure(home=self._home)
            except RuntimeError:
                # Opt-in was flipped off between the is_enabled check and
                # the id read.  Drop the event silently.
                return False
            envelope = build_envelope(name, install_id, payload)
            line = serialize_event(envelope)
            try:
                _append_local(line, self._home)
            except OSError as exc:
                _LOG.debug("telemetry: local queue append failed: %s", exc)
            self._post(line)
            return True
        except Exception as exc:
            _LOG.debug("telemetry: emit failed (suppressed): %s", exc)
            return False

    def _post(self, body: str) -> None:
        """POST the serialized event.  Failures are swallowed."""
        try:
            http = self._get_http()
            http.post(
                self._endpoint,
                content=body,
                headers={"content-type": "application/json"},
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:
            _LOG.debug("telemetry: POST failed (suppressed): %s", exc)

    def flush(self, deadline_seconds: float = FLUSH_DEADLINE_SECONDS) -> None:
        """Best-effort shutdown flush.  Bounded by ``deadline_seconds``."""
        deadline = time.monotonic() + deadline_seconds
        if time.monotonic() > deadline:
            return
        # No background queue today.  Kept for shape parity with future
        # async implementations.

    def close(self) -> None:
        """Close any client-owned HTTP resources."""
        if self._owns_http and self._http is not None:
            with contextlib.suppress(Exception):
                self._http.close()
            self._http = None

    def envelope_preview(
        self,
        name: TelemetryEvent,
        payload: EventPayload,
    ) -> EventEnvelope | None:
        """Return the envelope that ``emit`` would produce, or ``None``.

        Available even if telemetry is off and an install id has not been
        generated; in that case the result is ``None``.  Useful in tests.
        """
        if not self.is_enabled():
            return None
        existing = install_id_mod.read(self._home)
        if existing is None:
            return None
        return build_envelope(name, existing, payload)


# Module-level convenience.  Callers in main.py / worker.py use these.

_default_client: Client | None = None


def get_client() -> Client:
    """Return a process-wide default client."""
    global _default_client
    if _default_client is None:
        _default_client = Client()
    return _default_client


def reset_default_client() -> None:
    """Drop the cached default client.  Used by tests and ``telemetry off``."""
    global _default_client
    if _default_client is not None:
        _default_client.close()
    _default_client = None


__all__ = [
    "DEFAULT_ENDPOINT",
    "ENDPOINT_ENV",
    "FLUSH_DEADLINE_SECONDS",
    "QUEUE_ROTATION_DAYS",
    "TIMEOUT_SECONDS",
    "Client",
    "get_client",
    "read_recent_events",
    "reset_default_client",
]
