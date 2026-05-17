"""Append-only audit ledger for auto-heal.

Every heal attempt (shadow or live) emits exactly one JSONL line to
``.sdd/autoheal-history.jsonl``. The schema is operator-readable so the
companion CLI ``bernstein autoheal history --since 7d`` can pretty-print
the rows without joining external state.

Fields are intentionally flat (no nesting deeper than one level) so
the file can be ``jq``'d / ``rg``'d / ``awk``'d trivially in operator
sessions.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

Outcome = Literal[
    "applied",
    "skipped_no_jobs",
    "skipped_kill_switch",
    "skipped_idempotent",
    "skipped_budget",
    "shadow",
    "failed_validation",
    "failed_push",
    "escalated",
]


@dataclass(frozen=True, slots=True)
class HealRecord:
    """One row in the audit ledger.

    Attributes:
        ts: Unix epoch seconds.
        run_id: GitHub Actions ``workflow_run`` id that triggered the heal.
        head_sha: Failing commit SHA at the moment of triage.
        strategy: Repair strategy chosen (``"ruff-format"``, ...).
        cls: Coarse class (``"safe"`` / ``"heuristic"`` / ``"risky"`` /
            ``"unknown"``).
        confidence: Bayesian posterior at decision time, in ``[0, 1]``.
        outcome: One of the :data:`Outcome` literals.
        cost_usd: Total $$ spent on this attempt (LLM calls if any).
        llm_calls: Count of LLM round-trips.
        patch_sha: Git SHA of the heal patch (empty if not pushed).
        decision_id: ID of the matching decision-log entry.
        rationale: One-line operator-readable explanation.
    """

    ts: float
    run_id: str
    head_sha: str
    strategy: str
    cls: str
    confidence: float
    outcome: Outcome
    cost_usd: float = 0.0
    llm_calls: int = 0
    patch_sha: str = ""
    decision_id: str = ""
    rationale: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """Return one canonical JSON line (no trailing newline)."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def append(record: HealRecord, path: Path) -> None:
    """Append one record to the ledger (creates the file + parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.to_jsonl())
        fh.write("\n")


def iter_records(path: Path) -> Iterator[HealRecord]:
    """Iterate parsed records; malformed lines are skipped silently."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        try:
            yield HealRecord(
                ts=float(parsed.get("ts", 0.0)),
                run_id=str(parsed.get("run_id", "")),
                head_sha=str(parsed.get("head_sha", "")),
                strategy=str(parsed.get("strategy", "")),
                cls=str(parsed.get("cls", "")),
                confidence=float(parsed.get("confidence", 0.0)),
                outcome=_coerce_outcome(parsed.get("outcome", "skipped_no_jobs")),
                cost_usd=float(parsed.get("cost_usd", 0.0)),
                llm_calls=int(parsed.get("llm_calls", 0)),
                patch_sha=str(parsed.get("patch_sha", "")),
                decision_id=str(parsed.get("decision_id", "")),
                rationale=str(parsed.get("rationale", "")),
                meta=parsed.get("meta", {}) if isinstance(parsed.get("meta"), dict) else {},
            )
        except (TypeError, ValueError):
            continue


def coerce_outcome(value: object) -> Outcome:
    """Map an arbitrary value to one of the Outcome literals.

    Unknown values map to ``"skipped_no_jobs"`` as a safe default.
    Public alias of :func:`_coerce_outcome` for cross-module callers
    (e.g. :mod:`bernstein.core.autoheal.wire`).
    """
    allowed: tuple[Outcome, ...] = (
        "applied",
        "skipped_no_jobs",
        "skipped_kill_switch",
        "skipped_idempotent",
        "skipped_budget",
        "shadow",
        "failed_validation",
        "failed_push",
        "escalated",
    )
    if isinstance(value, str) and value in allowed:
        return value  # type: ignore[return-value]
    return "skipped_no_jobs"


def _coerce_outcome(value: object) -> Outcome:
    """Back-compat alias preserved for in-tree callers; prefer :func:`coerce_outcome`."""
    return coerce_outcome(value)


def now_record(
    *,
    run_id: str,
    head_sha: str,
    strategy: str,
    cls: str,
    confidence: float,
    outcome: Outcome,
    cost_usd: float = 0.0,
    llm_calls: int = 0,
    patch_sha: str = "",
    decision_id: str = "",
    rationale: str = "",
) -> HealRecord:
    """Build a record stamped with the current time."""
    return HealRecord(
        ts=time.time(),
        run_id=run_id,
        head_sha=head_sha,
        strategy=strategy,
        cls=cls,
        confidence=confidence,
        outcome=outcome,
        cost_usd=cost_usd,
        llm_calls=llm_calls,
        patch_sha=patch_sha,
        decision_id=decision_id,
        rationale=rationale,
    )


__all__ = [
    "HealRecord",
    "Outcome",
    "append",
    "coerce_outcome",
    "iter_records",
    "now_record",
]
