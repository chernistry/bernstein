"""File-backed ledger of per-(model, effort, phase) rework outcomes.

Closed-loop instrumentation for the cascade router. Every model attempt
(implement, review, plan, …) records a single sample to a JSONL file
shard; the cascade router consults aggregated rates to decide whether to
auto-promote a model class to its next tier.

The ledger is intentionally simple: append-only, atomic per-record write,
shard-by-fingerprint of ``(model, effort, phase)``. This matches the
"state lives in files" project philosophy and keeps reads cheap (the
shard for a given bucket is small).

Storage layout::

    .sdd/runtime/rework/<sha-prefix>.jsonl

Each line is a ``ReworkSample`` serialised as JSON.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

Outcome = Literal["success", "rework"]


@dataclass(frozen=True)
class ReworkSample:
    """One observed (model, effort, phase) attempt outcome.

    Attributes:
        model: Model identifier (e.g. ``"sonnet"``, ``"opus"``).
        effort: Effort tier (e.g. ``"low"``, ``"normal"``, ``"high"``, ``"max"``).
        phase: Phase identifier (e.g. ``"implement"``, ``"review"``, ``"plan"``).
        outcome: ``"success"`` or ``"rework"``.
        ts: Unix timestamp (seconds) of the observation.
        triggered_by: Optional free-form tag (e.g. ``"verifier"``,
            ``"phase_gate"``, ``"manager_requeue"``) — used for slicing.
    """

    model: str
    effort: str
    phase: str
    outcome: Outcome
    ts: float
    triggered_by: str = ""


@dataclass(frozen=True)
class ReworkRate:
    """Aggregate over a (model, effort, phase) bucket.

    Attributes:
        model: Bucket model identifier.
        effort: Bucket effort tier.
        phase: Bucket phase identifier.
        samples: Total number of observations in the window.
        rework: Count of ``rework`` outcomes.
        rate: ``rework / samples`` (0.0 when ``samples == 0``).
    """

    model: str
    effort: str
    phase: str
    samples: int
    rework: int
    rate: float


def _bucket_key(model: str, effort: str, phase: str) -> str:
    """Stable lower-case bucket key used for both shard naming and aggregation."""
    return f"{model.lower()}|{effort.lower()}|{phase.lower()}"


def _shard_name(model: str, effort: str, phase: str) -> str:
    """Return the JSONL shard filename for a (model, effort, phase) bucket."""
    digest = hashlib.sha256(_bucket_key(model, effort, phase).encode("utf-8")).hexdigest()
    return f"{digest[:16]}.jsonl"


class ReworkLedger:
    """Append-only, file-backed ledger of rework outcomes.

    The ledger is shard-per-bucket so each ``rework_rate(...)`` query reads
    only the relevant file. Writes use a thread-level lock plus an
    ``os.O_APPEND`` open so concurrent processes — and concurrent threads
    within one process — cannot interleave a single record. POSIX
    guarantees atomic writes for ``write()`` calls below ``PIPE_BUF`` to a
    file opened with ``O_APPEND``; our records are well under that bound.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        model: str,
        effort: str,
        phase: str,
        outcome: Outcome,
        triggered_by: str = "",
        ts: float | None = None,
    ) -> ReworkSample:
        """Append a single sample to the appropriate shard.

        Returns the persisted :class:`ReworkSample` (useful for tests and
        for surfacing the timestamp the caller observed).
        """
        sample = ReworkSample(
            model=model,
            effort=effort,
            phase=phase,
            outcome=outcome,
            ts=ts if ts is not None else time.time(),
            triggered_by=triggered_by,
        )
        path = self._shard_path(model, effort, phase)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = (json.dumps(asdict(sample), separators=(",", ":")) + "\n").encode("utf-8")
            # O_APPEND guarantees the seek-then-write is atomic at the
            # kernel level for sub-PIPE_BUF writes — no interleaving.
            # 0o600: rework telemetry contains (model, effort, phase, outcome)
            # records that the cascade router replays. Reader and writer are
            # the same operator user; world-read is unnecessary and would
            # leak routing decisions to other users on shared hosts.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with self._lock:
                    os.write(fd, payload)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("rework_ledger: append failed for %s: %s", path, exc)
        return sample

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def rework_rate(
        self,
        *,
        model: str,
        effort: str,
        phase: str,
        window_hours: float | None = None,
    ) -> ReworkRate:
        """Compute the rework rate for a bucket.

        Args:
            model: Model identifier.
            effort: Effort tier.
            phase: Phase identifier.
            window_hours: When set, ignore samples older than this many hours.

        Returns:
            A :class:`ReworkRate` with sample count and rate. ``rate`` is
            ``0.0`` when the bucket is empty.
        """
        cutoff = (time.time() - window_hours * 3600.0) if window_hours is not None else None
        samples = 0
        rework = 0
        for sample in self._iter_shard(model, effort, phase):
            if cutoff is not None and sample.ts < cutoff:
                continue
            samples += 1
            if sample.outcome == "rework":
                rework += 1
        rate = (rework / samples) if samples > 0 else 0.0
        return ReworkRate(
            model=model.lower(),
            effort=effort.lower(),
            phase=phase.lower(),
            samples=samples,
            rework=rework,
            rate=rate,
        )

    def _iter_shard(self, model: str, effort: str, phase: str) -> list[ReworkSample]:
        path = self._shard_path(model, effort, phase)
        if not path.exists():
            return []
        out: list[ReworkSample] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("rework_ledger: read failed for %s: %s", path, exc)
            return []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            with contextlib.suppress(KeyError, TypeError, ValueError):
                outcome = str(payload.get("outcome", "success"))
                if outcome not in ("success", "rework"):
                    continue
                out.append(
                    ReworkSample(
                        model=str(payload["model"]),
                        effort=str(payload["effort"]),
                        phase=str(payload["phase"]),
                        outcome="rework" if outcome == "rework" else "success",
                        ts=float(payload["ts"]),
                        triggered_by=str(payload.get("triggered_by", "")),
                    )
                )
        return out

    def _shard_path(self, model: str, effort: str, phase: str) -> Path:
        return self._root / _shard_name(model, effort, phase)


# ---------------------------------------------------------------------------
# Singleton helper — every cascade-router consumer should reuse one ledger
# rooted at ``<workdir>/.sdd/runtime/rework`` so all phases share state.
# ---------------------------------------------------------------------------

_default_ledgers: dict[Path, ReworkLedger] = {}
_default_lock = threading.Lock()


def default_ledger(workdir: Path) -> ReworkLedger:
    """Return a process-singleton ledger rooted at ``<workdir>/.sdd/runtime/rework``."""
    with _default_lock:
        cached = _default_ledgers.get(workdir)
        if cached is None:
            cached = ReworkLedger(root=workdir / ".sdd" / "runtime" / "rework")
            _default_ledgers[workdir] = cached
        return cached


__all__ = [
    "Outcome",
    "ReworkLedger",
    "ReworkRate",
    "ReworkSample",
    "default_ledger",
]
