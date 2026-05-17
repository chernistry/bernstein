"""Record/replay gateway for LLM requests and tool dispatch.

The gateway sits between Bernstein's adapter call-sites and the live
providers. In *record* mode every (kind, key) -> response pair is appended
to ``.sdd/runs/<run_id>/events.jsonl``. In *replay* mode the gateway
serves the recorded response instead of invoking the real provider, so a
run can be re-executed deterministically against recorded fixtures.

Design choices:

* **Append-only JSONL** — same on-disk shape as existing trace files; works
  with the rest of the observability stack and stays human-diffable.
* **Recording is opt-in** — controlled by :data:`RECORD_ENV_VAR` or an
  explicit ``record=True`` argument. We don't want to bloat ``.sdd/`` for
  users who never replay.
* **Stable keys** — callers pass an explicit ``key`` (typically a SHA-256
  of the request payload). The gateway never tries to fingerprint the
  request itself; key stability is the caller's job.
* **First-call ordering preserved** — replay lookup falls back to FIFO
  consumption per ``kind`` when the key isn't found, so even hashed
  prompts with timestamp jitter replay cleanly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

#: Name of the per-run gateway event log inside ``.sdd/runs/<id>/``.
EVENTS_FILENAME = "events.jsonl"

#: Environment variable that opts the gateway into record mode.
#: Recording stays off by default to avoid growing ``.sdd/`` on every
#: invocation. Set to ``1``/``true``/``yes`` to enable.
RECORD_ENV_VAR = "BERNSTEIN_RECORD"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_recording_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether the gateway should record this run by default.

    Args:
        env: Optional env dict (defaults to :data:`os.environ`).

    Returns:
        ``True`` if :data:`RECORD_ENV_VAR` is set to a truthy value.
    """
    src = env if env is not None else os.environ
    return src.get(RECORD_ENV_VAR, "").strip().lower() in _TRUTHY


class GatewayMode(StrEnum):
    """Operating mode for :class:`ReplayGateway`."""

    OFF = "off"
    """Pass-through; no recording, no replay."""

    RECORD = "record"
    """Invoke the live provider and append each response to ``events.jsonl``."""

    REPLAY = "replay"
    """Serve recorded responses; never call the live provider."""


class ReplayMissError(RuntimeError):
    """Raised in :attr:`GatewayMode.REPLAY` when no fixture matches."""


@dataclass(frozen=True)
class _Event:
    """One row from ``events.jsonl``."""

    kind: str
    key: str
    response: Any
    ts: float
    seq: int


class ReplayGateway:
    """Thin wrapper around LLM + tool dispatch with record/replay support.

    Typical usage from an adapter call-site::

        gw = ReplayGateway(run_id="20260517-1530", sdd_dir=Path(".sdd"))
        text = gw.dispatch(
            kind="llm",
            key=request_hash,
            invoke=lambda: real_llm_client.complete(prompt),
        )

    With ``BERNSTEIN_RECORD=1`` (or ``ReplayGateway(record=True)``) the
    response from ``invoke`` is appended to ``events.jsonl``. In replay
    mode, ``invoke`` is **not** called; the recorded response is returned
    instead.

    Args:
        run_id: Unique identifier for this run; used to locate the
            per-run event log under ``.sdd/runs/<run_id>/``.
        sdd_dir: Path to the ``.sdd`` directory.
        mode: Explicit :class:`GatewayMode`. If omitted, defaults to
            :attr:`GatewayMode.RECORD` when :func:`is_recording_enabled`
            is true and ``record`` is not set, else :attr:`GatewayMode.OFF`.
        record: Convenience flag — when ``True``, forces record mode even
            if the env var is unset. Ignored if ``mode`` is provided.
    """

    def __init__(
        self,
        run_id: str,
        sdd_dir: Path,
        *,
        mode: GatewayMode | None = None,
        record: bool = False,
    ) -> None:
        self._run_id = run_id
        self._path = sdd_dir / "runs" / run_id / EVENTS_FILENAME
        self._lock = threading.Lock()
        self._seq = 0

        if mode is None:
            mode = GatewayMode.RECORD if record or is_recording_enabled() else GatewayMode.OFF
        self._mode = mode

        # Replay-mode fixture state.
        self._fixtures_by_key: dict[tuple[str, str], list[Any]] = {}
        self._fixtures_by_kind: dict[str, list[Any]] = {}

        if self._mode is GatewayMode.RECORD:
            # Only create the directory when we'll actually write something.
            # Replay mode reads existing files; OFF mode does nothing.
            self._path.parent.mkdir(parents=True, exist_ok=True)
        elif self._mode is GatewayMode.REPLAY:
            self._load_fixtures()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> GatewayMode:
        """Current operating mode."""
        return self._mode

    @property
    def path(self) -> Path:
        """Path to ``events.jsonl`` for this run."""
        return self._path

    @property
    def run_id(self) -> str:
        """The run identifier this gateway targets."""
        return self._run_id

    def dispatch(
        self,
        *,
        kind: str,
        key: str,
        invoke: Callable[[], Any],
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Run a recorded/replayed dispatch.

        Args:
            kind: Logical category (e.g. ``"llm"``, ``"tool"``). Used to
                bucket replay fixtures when keys collide.
            key: Stable identifier for this request (typically a hash of
                the request payload). Replay lookups try ``(kind, key)``
                first, then fall back to FIFO consumption of ``kind``.
            invoke: Callable that performs the real dispatch. Called in
                :attr:`GatewayMode.OFF` and :attr:`GatewayMode.RECORD`;
                **never** called in :attr:`GatewayMode.REPLAY`.
            metadata: Optional extra fields persisted alongside the event
                (e.g. model name, adapter name) for debugging.

        Returns:
            The response (either from ``invoke`` or from the fixture).

        Raises:
            ReplayMissError: In replay mode when no fixture matches and
                no FIFO fallback is available for ``kind``.
        """
        if self._mode is GatewayMode.REPLAY:
            return self._replay_lookup(kind=kind, key=key)

        response = invoke()

        if self._mode is GatewayMode.RECORD:
            self._record(kind=kind, key=key, response=response, metadata=metadata)

        return response

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _record(
        self,
        *,
        kind: str,
        key: str,
        response: Any,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Append one event to ``events.jsonl``."""
        with self._lock:
            self._seq += 1
            seq = self._seq

        entry: dict[str, Any] = {
            "seq": seq,
            "ts": time.time(),
            "kind": kind,
            "key": key,
            "response": _make_jsonable(response),
        }
        if metadata:
            entry["metadata"] = _make_jsonable(metadata)

        try:
            with self._path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            # Recording is a debug aid; failures must not break the run.
            logger.warning("ReplayGateway: failed to record %r: %s", kind, exc)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def _load_fixtures(self) -> None:
        """Load fixtures from ``events.jsonl`` into in-memory queues."""
        if not self._path.exists():
            raise ReplayMissError(
                f"No events log at {self._path}; nothing to replay. "
                "Was BERNSTEIN_RECORD=1 set during the original run?",
            )
        with self._path.open() as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("ReplayGateway: skipping malformed line in %s", self._path)
                    continue
                kind = str(row.get("kind", ""))
                key = str(row.get("key", ""))
                response = row.get("response")
                self._fixtures_by_key.setdefault((kind, key), []).append(response)
                self._fixtures_by_kind.setdefault(kind, []).append(response)

    def _replay_lookup(self, *, kind: str, key: str) -> Any:
        """Pop the next fixture for ``(kind, key)`` (or FIFO by ``kind``)."""
        bucket = self._fixtures_by_key.get((kind, key))
        if bucket:
            response = bucket.pop(0)
            # Also remove the matching entry from the by-kind queue
            # (FIFO match — first identical response).
            kind_bucket = self._fixtures_by_kind.get(kind, [])
            for i, item in enumerate(kind_bucket):
                if item == response:
                    kind_bucket.pop(i)
                    break
            return response

        kind_bucket = self._fixtures_by_kind.get(kind)
        if kind_bucket:
            return kind_bucket.pop(0)

        raise ReplayMissError(
            f"No fixture for kind={kind!r} key={key!r} in {self._path}. "
            "Either the run diverged or recording was incomplete.",
        )


def _make_jsonable(value: Any) -> Any:
    """Best-effort coercion of ``value`` to JSON-serialisable shape.

    Falls back to ``repr`` for opaque objects; primitive types and
    standard containers are passed through unchanged.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_make_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _make_jsonable(v) for k, v in value.items()}
    # Dataclasses, pydantic, custom objects: try dict-like, then repr.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _make_jsonable(to_dict())
        except (TypeError, ValueError):
            pass
    return repr(value)
