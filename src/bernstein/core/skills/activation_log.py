"""Append-only activation log for loaded skills (issue #1720, Track 5 floor).

Every time a skill is loaded into a spawn, the orchestrator appends a
structured record to ``.sdd/skills/activations.jsonl`` under the project
root. The log is local-only; there is no maintainer endpoint and no
network egress. Operators opt out by setting
``BERNSTEIN_SKILL_ACTIVATION_LOG=0`` (or any of the falsy aliases
``false`` / ``no`` / ``off``) in their environment.

The log line schema is intentionally narrow so a future PR can pivot to
a structured-binary format without breaking compatibility:

.. code-block:: json

    {
      "skill": "bernstein-test-runner",
      "version": "1.0.0",
      "digest": "<blake2b hex>",
      "role": "backend",
      "task_id": "task-42",
      "trigger_source": "role-binding",
      "timestamp": "2026-05-20T12:34:56.789Z"
    }

The file is created lazily on first write so a spawn that does not load
any skills leaves no trace. Failures during append are swallowed and
logged at WARNING so an unwritable disk never blocks a spawn.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - runtime annotation in helper functions
from typing import Final

logger = logging.getLogger(__name__)

#: Env var controlling whether the activation log is written.
ENV_VAR: Final[str] = "BERNSTEIN_SKILL_ACTIVATION_LOG"

#: Subpath relative to the project workdir.
_LOG_SUBPATH: Final[tuple[str, ...]] = (".sdd", "skills", "activations.jsonl")

#: Values that disable the log when assigned to the env var.
_DISABLE_TOKENS: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


def is_logging_enabled() -> bool:
    """Return whether activation logging is enabled for this process.

    The default is ``True``. Operators opt out by setting
    :data:`ENV_VAR` to any value in :data:`_DISABLE_TOKENS` (case
    insensitive).
    """
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    return raw not in _DISABLE_TOKENS


def activation_log_path(workdir: Path) -> Path:
    """Return the on-disk location of the activation log."""
    return workdir.joinpath(*_LOG_SUBPATH)


@dataclass(frozen=True)
class ActivationRecord:
    """One structured row in the activation log.

    Optional fields default to empty strings so the JSON shape is stable
    even when the orchestrator does not know the task id (e.g. ad-hoc
    ``bernstein skills show`` calls).
    """

    skill: str
    role: str = ""
    task_id: str = ""
    trigger_source: str = ""
    version: str = ""
    digest: str = ""

    def as_payload(self, *, now: datetime | None = None) -> dict[str, str]:
        """Render the record as the JSON dict written to the log."""
        ts = (now or datetime.now(tz=UTC)).isoformat(timespec="milliseconds")
        # Match the RFC sample exactly: trailing ``Z`` instead of
        # ``+00:00``. ``datetime.isoformat`` produces the latter; we
        # rewrite the suffix so the log is grep-friendly.
        if ts.endswith("+00:00"):
            ts = ts[: -len("+00:00")] + "Z"
        return {
            "skill": self.skill,
            "version": self.version,
            "digest": self.digest,
            "role": self.role,
            "task_id": self.task_id,
            "trigger_source": self.trigger_source,
            "timestamp": ts,
        }


def log_activation(
    record: ActivationRecord,
    *,
    workdir: Path,
    now: datetime | None = None,
) -> Path | None:
    """Append one activation record to the per-project log.

    Args:
        record: The activation record to write.
        workdir: Project root. The log lands at
            ``<workdir>/.sdd/skills/activations.jsonl``.
        now: Override for the timestamp (tests).

    Returns:
        The log path when a line was written; ``None`` when logging is
        disabled or the append failed (the failure is logged at WARNING).
    """
    if not is_logging_enabled():
        return None
    path = activation_log_path(workdir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.as_payload(now=now), separators=(",", ":"), sort_keys=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
    except OSError as exc:
        logger.warning("skills.activation_log_write_failed path=%s error=%s", path, exc)
        return None
    return path


__all__ = [
    "ENV_VAR",
    "ActivationRecord",
    "activation_log_path",
    "is_logging_enabled",
    "log_activation",
]
