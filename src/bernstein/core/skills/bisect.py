"""Local skill activation bisect planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from bernstein.core.observability.metric_collector import iter_metric_files
from bernstein.core.skills.activation_log import activation_log_path

if TYPE_CHECKING:
    from pathlib import Path

_METRIC_TYPE: Final[str] = "task_completion_time"


class SkillBisectError(ValueError):
    """Raised when a bisect plan cannot be built from local files."""


@dataclass(frozen=True)
class SkillBisectCandidate:
    """One skill activated for a task."""

    skill: str
    role: str
    trigger_source: str
    version: str
    digest: str
    timestamp: str

    def as_payload(self) -> dict[str, str]:
        """Render this candidate as stable JSON."""
        return {
            "skill": self.skill,
            "role": self.role,
            "trigger_source": self.trigger_source,
            "version": self.version,
            "digest": self.digest,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SkillBisectProbe:
    """Next deterministic probe for a skill bisect."""

    disable: tuple[str, ...]
    keep: tuple[str, ...]

    def as_payload(self) -> dict[str, list[str]]:
        """Render this probe as stable JSON."""
        return {
            "disable": list(self.disable),
            "keep": list(self.keep),
        }


@dataclass(frozen=True)
class SkillBisectPlan:
    """A deterministic local bisect plan for one task."""

    task_id: str
    outcome: str
    candidates: tuple[SkillBisectCandidate, ...]
    next_probe: SkillBisectProbe

    @property
    def candidate_count(self) -> int:
        """Return the number of candidate skills in the plan."""
        return len(self.candidates)

    def as_payload(self) -> dict[str, object]:
        """Render this plan as stable JSON."""
        return {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "candidate_count": self.candidate_count,
            "next_probe": self.next_probe.as_payload(),
            "candidates": [candidate.as_payload() for candidate in self.candidates],
        }


def build_skill_bisect_plan(workdir: Path, task_id: str) -> SkillBisectPlan:
    """Build a deterministic bisect plan from activation and metrics files."""
    candidates = _load_task_candidates(workdir, task_id)
    if not candidates:
        raise SkillBisectError(f"{task_id}: no activations found")
    midpoint = max(1, len(candidates) // 2)
    disabled = tuple(candidate.skill for candidate in candidates[:midpoint])
    kept = tuple(candidate.skill for candidate in candidates[midpoint:])
    return SkillBisectPlan(
        task_id=task_id,
        outcome=_task_outcome(workdir, task_id),
        candidates=candidates,
        next_probe=SkillBisectProbe(disable=disabled, keep=kept),
    )


def _load_task_candidates(workdir: Path, task_id: str) -> tuple[SkillBisectCandidate, ...]:
    seen: set[str] = set()
    candidates: list[SkillBisectCandidate] = []
    for row in _iter_jsonl_objects(activation_log_path(workdir)):
        if _string_field(row, "task_id") != task_id:
            continue
        skill = _string_field(row, "skill")
        if not skill or skill in seen:
            continue
        seen.add(skill)
        candidates.append(
            SkillBisectCandidate(
                skill=skill,
                role=_string_field(row, "role"),
                trigger_source=_string_field(row, "trigger_source"),
                version=_string_field(row, "version"),
                digest=_string_field(row, "digest"),
                timestamp=_string_field(row, "timestamp"),
            )
        )
    return tuple(candidates)


def _task_outcome(workdir: Path, task_id: str) -> str:
    latest_key: tuple[float, int] | None = None
    latest_success: bool | None = None
    sequence = 0
    for path in iter_metric_files(workdir / ".sdd" / "metrics", _METRIC_TYPE):
        for row in _iter_jsonl_objects(path):
            labels = _mapping_field(row, "labels")
            if labels is None or _string_field(labels, "task_id") != task_id:
                continue
            sequence += 1
            timestamp = _numeric_field(row, "timestamp")
            candidate_key = (timestamp if timestamp is not None else float("-inf"), sequence)
            if latest_key is None or candidate_key > latest_key:
                latest_key = candidate_key
                latest_success = _success_value(labels.get("success"))
    if latest_success is True:
        return "passed"
    if latest_success is False:
        return "failed"
    return "unknown"


def _iter_jsonl_objects(path: Path) -> tuple[dict[str, object], ...]:
    if not path.is_file():
        return ()
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        row: dict[str, object] = {}
        parsed_dict = cast("dict[object, object]", parsed)
        for key, value in parsed_dict.items():
            if isinstance(key, str):
                row[key] = value
        rows.append(row)
    return tuple(rows)


def _mapping_field(row: dict[str, object], key: str) -> dict[str, object] | None:
    value = row.get(key)
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    value_dict = cast("dict[object, object]", value)
    for nested_key, nested_value in value_dict.items():
        if isinstance(nested_key, str):
            out[nested_key] = nested_value
    return out


def _string_field(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _numeric_field(row: dict[str, object], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _success_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"true", "1", "yes"}:
            return True
        if normalised in {"false", "0", "no"}:
            return False
    return None


__all__ = [
    "SkillBisectCandidate",
    "SkillBisectError",
    "SkillBisectPlan",
    "SkillBisectProbe",
    "build_skill_bisect_plan",
]
