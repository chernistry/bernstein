"""Structured decision log for routing, profile, and gate choices.

Every routing or criterion-profile decision in Bernstein writes an
append-only JSONL record to ``.sdd/runtime/decisions.jsonl`` so that
operators can reconstruct *why* a particular model, mode, or profile
was chosen without re-reading 4 modules of cross-referenced code.

Schema (``schema_version=1``)::

    {
        "schema_version": 1,
        "ts": 1700000000.123,
        "decision_id": "dec-...",
        "kind": "model_route" | "mode_profile" | "criterion_profile"
                | "gate_fire" | "autoheal_strategy" | "tier3_shadow"
                | "cordon_violation" | "recurrence_escalation",
        "chosen": "<winner id>",
        "alternatives": [{"id": "...", "score": 0.0, "reason": "..."}, ...],
        "confidence": 0.0 .. 1.0,
        "rationale": "human-readable",
        "parent_decision_id": "dec-..." | None,
        "policy_path": ["policy_a", "policy_b", ...],
        "winner_score": 0.0,
        "inputs": {"task_id": "...", ...}
    }

Disabling the log: set ``BERNSTEIN_DECISION_LOG=0`` in the environment.
The :func:`record_decision` entry point becomes a no-op; routing paths
remain unchanged.

Design constraints honoured by this module:

* **Append-only**. Records are appended with a single ``write()`` call
  on a file opened in ``"a"`` mode; the kernel guarantees per-line
  atomicity below ``PIPE_BUF`` for the byte sizes produced here. This
  matches the pattern used by ``.sdd/runtime/abandons.jsonl`` and the
  existing ``tick_telemetry`` writer.
* **Concurrent-safe**. A module-level :class:`threading.Lock` serialises
  writes within a process. Cross-process safety is provided by the
  append-mode open + small line sizes.
* **No silent corruption**. ``iter_records`` skips malformed lines but
  logs them at debug level; ``replay`` returns only well-formed records.
* **Bounded payloads**. ``alternatives`` is truncated to
  :data:`MAX_ALTERNATIVES` entries before serialisation so a routing
  bug that explores 10000 candidates cannot run the disk out of space.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1
"""Current decision-log schema version.

Bumping this requires updating :func:`_migrate_record` and asserting the
new version in tests/unit/test_decision_log.py::test_schema_version.
"""

MAX_ALTERNATIVES: int = 64
"""Maximum number of alternative candidates persisted per record.

Anything beyond this is dropped silently - the routing path always
fits comfortably under this cap; the bound exists to defend against
pathological policy bugs."""

MAX_RATIONALE_LEN: int = 4096
"""Maximum rationale string length. Truncated with an ellipsis suffix."""

VALID_KINDS: frozenset[str] = frozenset(
    {
        "model_route",
        "mode_profile",
        "criterion_profile",
        "gate_fire",
        "autoheal_strategy",
        "tier3_shadow",
        "cordon_violation",
        "recurrence_escalation",
    }
)
"""Decision kinds known to the schema.

Unknown kinds are *rejected* at write time so that downstream readers
can rely on a closed-set vocabulary. New kinds must be added here AND
documented in the module docstring before they are emitted.

* ``autoheal_strategy`` is emitted by ``core.autoheal.wire`` so heal
  actions appear alongside routing / profile / gate decisions in the
  same operator surface (``bernstein decisions tail``).
* ``tier3_shadow`` / ``cordon_violation`` / ``recurrence_escalation``
  are emitted by ``core.autofix.tier3`` for the OpenRouter free-tier
  shadow-mode self-driving CI escalation. ``tier3_shadow`` records a
  captured (but not pushed) patch; ``cordon_violation`` records a
  refusal because the patch touched out-of-cordon paths;
  ``recurrence_escalation`` records the hand-off to Tier-4 (operator
  notification) when the same failure class / test nodeid has been
  fixed too often in the recurrence window.
"""

ENV_DISABLE = "BERNSTEIN_DECISION_LOG"
"""Environment variable name; setting to ``"0"`` disables the writer."""

DEFAULT_PATH = Path(".sdd/runtime/decisions.jsonl")
"""Default on-disk location for the append-only ledger."""

# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alternative:
    """One non-winning candidate considered during a decision.

    Attributes:
        id: Stable candidate identifier (e.g. model name, profile name).
        score: Numeric score the policy assigned to this candidate.
        reason: Optional short string explaining why it lost.
    """

    id: str
    score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {"id": self.id, "score": self.score, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Alternative:
        """Build from a dict (e.g. parsed JSONL line)."""
        return cls(
            id=str(data.get("id", "")),
            score=float(data.get("score", 0.0)),
            reason=str(data.get("reason", "")),
        )


@dataclass(frozen=True)
class DecisionRecord:
    """A single routing/profile/gate decision.

    See module docstring for the full schema. All fields are required
    in v1 except ``parent_decision_id``, ``policy_path``, and
    ``inputs`` (which default to empty/None).
    """

    ts: float
    decision_id: str
    kind: str
    chosen: str
    alternatives: tuple[Alternative, ...]
    confidence: float
    rationale: str
    parent_decision_id: str | None = None
    policy_path: tuple[str, ...] = ()
    winner_score: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict[str, Any])
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        Field order is stable so snapshot-based parser tests do not flap
        across CPython minor versions.
        """
        return {
            "schema_version": self.schema_version,
            "ts": self.ts,
            "decision_id": self.decision_id,
            "kind": self.kind,
            "chosen": self.chosen,
            "winner_score": self.winner_score,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "policy_path": list(self.policy_path),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "parent_decision_id": self.parent_decision_id,
            "inputs": self.inputs.copy(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        """Build a record from a parsed JSONL line.

        Tolerates missing optional fields (forward-compat with v1+ readers
        seeing older records). Raises ``ValueError`` if a required field
        is missing or has the wrong type.
        """
        required = ("ts", "decision_id", "kind", "chosen")
        for r in required:
            if r not in data:
                raise ValueError(f"missing required field: {r}")

        kind = str(data["kind"])
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown decision kind: {kind!r}")

        alts_raw: Any = data.get("alternatives", []) or []
        if not isinstance(alts_raw, list):
            raise ValueError("alternatives must be a list")
        alts_list = cast(list[Any], alts_raw)
        alts = tuple(Alternative.from_dict(cast(dict[str, Any], a)) for a in alts_list if isinstance(a, dict))

        policy_path_raw: Any = data.get("policy_path", []) or []
        if not isinstance(policy_path_raw, list):
            raise ValueError("policy_path must be a list")
        policy_path_list = cast(list[Any], policy_path_raw)

        inputs_raw: Any = data.get("inputs", {}) or {}
        if not isinstance(inputs_raw, dict):
            raise ValueError("inputs must be a dict")
        inputs_typed = cast(dict[Any, Any], inputs_raw)
        inputs_dict: dict[str, Any] = {str(k): v for k, v in inputs_typed.items()}

        return cls(
            ts=float(data["ts"]),
            decision_id=str(data["decision_id"]),
            kind=kind,
            chosen=str(data["chosen"]),
            alternatives=alts,
            confidence=float(data.get("confidence", 0.0)),
            rationale=str(data.get("rationale", "")),
            parent_decision_id=(str(data["parent_decision_id"]) if data.get("parent_decision_id") else None),
            policy_path=tuple(str(p) for p in policy_path_list),
            winner_score=float(data.get("winner_score", 0.0)),
            inputs=inputs_dict,
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


_WRITE_LOCK = threading.Lock()


def _is_disabled() -> bool:
    """Return True when ``BERNSTEIN_DECISION_LOG=0`` in the environment.

    The guard is centralised so the rest of the module can stay pure.
    """
    return os.environ.get(ENV_DISABLE, "1") == "0"


def _clamp_alternatives(alts: Iterable[Alternative]) -> tuple[Alternative, ...]:
    """Truncate alternatives to :data:`MAX_ALTERNATIVES`.

    The cap exists to defend the writer against pathological policies
    that might produce thousands of candidates. We keep the first N -
    well-behaved callers always pass small lists ordered by descending
    score so the truncation is harmless when it does fire.
    """
    items = list(alts)
    if len(items) <= MAX_ALTERNATIVES:
        return tuple(items)
    logger.debug("Truncating %d alternatives to %d", len(items), MAX_ALTERNATIVES)
    return tuple(items[:MAX_ALTERNATIVES])


def _truncate_rationale(rationale: str) -> str:
    """Cap rationale length so an entry never blows up the JSONL line."""
    if len(rationale) <= MAX_RATIONALE_LEN:
        return rationale
    return rationale[: MAX_RATIONALE_LEN - 1] + "…"


def new_decision_id() -> str:
    """Mint a fresh decision id.

    Format: ``"dec-" + hex(uuid4)``. The prefix lets a human reader spot
    decision ids in mixed logs at a glance; the uuid4 body gives 122 bits
    of entropy which is plenty for in-process or cross-process uniqueness.
    """
    return "dec-" + uuid.uuid4().hex


def record_decision(
    *,
    kind: str,
    chosen: str,
    rationale: str = "",
    confidence: float = 0.0,
    alternatives: Iterable[Alternative] = (),
    winner_score: float = 0.0,
    policy_path: Iterable[str] = (),
    parent_decision_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    path: Path | None = None,
    ts: float | None = None,
) -> DecisionRecord | None:
    """Append one decision to the JSONL ledger.

    Args:
        kind: One of :data:`VALID_KINDS`.
        chosen: Stable identifier of the winning candidate.
        rationale: Human-readable explanation; truncated at
            :data:`MAX_RATIONALE_LEN`.
        confidence: ``0.0..1.0`` confidence score.
        alternatives: Non-winning candidates; truncated at
            :data:`MAX_ALTERNATIVES`.
        winner_score: Numeric score of the winning candidate.
        policy_path: Ordered list of policy names that voted.
        parent_decision_id: Decision id this decision was nested under,
            e.g. a ``mode_profile`` decision that gated a ``model_route``.
        inputs: Free-form input dict (task id, complexity, etc.).
        path: Override the default JSONL destination (testing).
        ts: Override the timestamp (testing/replay).

    Returns:
        The persisted :class:`DecisionRecord`, or ``None`` when the
        writer is disabled via :data:`ENV_DISABLE`.

    Raises:
        ValueError: When ``kind`` is not in :data:`VALID_KINDS` or
            ``confidence`` is outside ``[0.0, 1.0]``.
    """
    if _is_disabled():
        return None

    if kind not in VALID_KINDS:
        raise ValueError(f"unknown decision kind: {kind!r}; allowed: {sorted(VALID_KINDS)}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0]; got {confidence!r}")
    if not chosen:
        raise ValueError("chosen must be a non-empty string")

    record = DecisionRecord(
        ts=time.time() if ts is None else float(ts),
        decision_id=new_decision_id(),
        kind=kind,
        chosen=chosen,
        alternatives=_clamp_alternatives(alternatives),
        confidence=confidence,
        rationale=_truncate_rationale(rationale),
        parent_decision_id=parent_decision_id,
        policy_path=tuple(policy_path),
        winner_score=winner_score,
        inputs=dict(inputs or {}),
    )

    dest = path if path is not None else DEFAULT_PATH
    _append_jsonl(dest, record)
    return record


def _append_jsonl(path: Path, record: DecisionRecord) -> None:
    """Append a record to *path* under the module write lock.

    The lock is process-local; for cross-process correctness we rely on
    POSIX append-mode semantics (each ``write()`` is atomic when the
    payload is smaller than ``PIPE_BUF``, typically 4 KiB).
    """
    line = json.dumps(record.to_dict(), separators=(",", ":"), sort_keys=False) + "\n"
    with _WRITE_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            # Failing to record a decision must never break the routing path.
            logger.warning("decision log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Reader / replay
# ---------------------------------------------------------------------------


def iter_records(path: Path | None = None) -> Iterator[DecisionRecord]:
    """Iterate over well-formed records in *path*.

    Malformed lines are skipped (and logged at debug level). The iterator
    is lazy: callers can stop early without parsing the rest of the file.
    """
    dest = path if path is not None else DEFAULT_PATH
    if not dest.exists():
        return

    with dest.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                data: Any = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
                data_typed = cast(dict[Any, Any], data)
                typed: dict[str, Any] = {str(k): v for k, v in data_typed.items()}
                yield DecisionRecord.from_dict(_migrate_record(typed))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.debug("skip malformed decision-log line %d: %s", lineno, exc)
                continue


def replay(path: Path | None = None) -> list[DecisionRecord]:
    """Read every well-formed record from *path*.

    Returned records preserve write order, which is also non-decreasing
    by ``ts`` for any single-process session.
    """
    return list(iter_records(path))


def _migrate_record(data: dict[str, Any]) -> dict[str, Any]:
    """Apply schema migrations so v1 readers can handle v1 records.

    Currently a no-op; the function exists so a future schema bump
    can plug migrations in here without touching call sites.
    """
    version = int(data.get("schema_version", SCHEMA_VERSION))
    if version > SCHEMA_VERSION:
        raise ValueError(f"record schema_version {version} newer than reader {SCHEMA_VERSION}")
    return data


# ---------------------------------------------------------------------------
# Query helpers (used by the CLI)
# ---------------------------------------------------------------------------


def filter_by_kind(records: Iterable[DecisionRecord], kind: str) -> list[DecisionRecord]:
    """Return only records whose ``kind`` matches *kind*."""
    return [r for r in records if r.kind == kind]


def filter_since(records: Iterable[DecisionRecord], cutoff_ts: float) -> list[DecisionRecord]:
    """Return records with ``ts >= cutoff_ts``."""
    return [r for r in records if r.ts >= cutoff_ts]


def parse_duration(spec: str) -> float:
    """Parse a duration like ``"10s"``, ``"15m"``, ``"2h"``, ``"1d"`` to seconds.

    Args:
        spec: Duration spec; the trailing unit char picks the multiplier.
            A bare integer is interpreted as seconds.

    Raises:
        ValueError: When the spec is empty, unparseable, or uses an
            unsupported unit.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty duration")
    units = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    if spec[-1] in units:
        amount = spec[:-1]
        mult = units[spec[-1]]
    else:
        amount = spec
        mult = 1.0
    try:
        return float(amount) * mult
    except ValueError as exc:
        raise ValueError(f"invalid duration {spec!r}") from exc
