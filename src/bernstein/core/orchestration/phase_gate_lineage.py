"""Per-artifact lineage hook for phase-gate boundary events.

Each phase boundary writes a lineage record so the audit trail becomes
per-phase, per-rule.  We reuse the existing
:class:`bernstein.core.persistence.lineage.LineageWriter` rather than
creating a parallel store - verifying the WAL hash chain
(``WALReader.verify_chain``) and the audit-log HMAC chain remains a
single operation.

Record shape (mapped onto :class:`LineageRecord`)::

    output_artifact -> .sdd/runtime/phase_artifacts/<task_id>/<phase>.json
                       (the just-written artefact)
    inputs          -> empty list; the prior phase's artefact is already
                       on the lineage chain via its own write event
    producer        -> AgentRef(agent_id="phase_gate", run_id=task.id)
    prompt_sha      -> stable hash of "<rule_id>:<outcome>" entries so
                       two replays of the same evaluation are bit-identical
    model           -> phase id (used as a free-form tag)

The ``regulatory_class`` field is set to ``"phase_gate"`` so compliance
filters can pull every gate event in one query.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from bernstein.core.persistence.lineage import (
    AgentRef,
    ArtifactRef,
    LineageRecord,
    LineageWriter,
    hash_file,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bernstein.core.orchestration.phase_gates import GateResult
    from bernstein.core.orchestration.phase_pipeline import Phase
    from bernstein.core.tasks.models import Task


PHASE_GATE_REGULATORY_CLASS = "phase_gate"


def gate_results_summary(results: list[GateResult]) -> dict[str, Any]:
    """Render *results* as a JSON-friendly summary for audit consumers."""
    return {
        "rules": [
            {
                "rule_id": r.rule_id,
                "outcome": r.outcome.value,
                "boundary_from": r.boundary_from.value if r.boundary_from is not None else None,
                "boundary_to": r.boundary_to.value if r.boundary_to is not None else None,
                "details": r.details,
            }
            for r in results
        ]
    }


def _prompt_sha(results: list[GateResult]) -> str:
    """Stable hash of the rule outcomes - replay-friendly fn_hash."""
    canonical = json.dumps(
        [(r.rule_id, r.outcome.value) for r in results],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_phase_gate_record(
    *,
    task: Task,
    phase: Phase,
    boundary: tuple[Phase, Phase],
    results: list[GateResult],
    artifact_path: Path,
) -> LineageRecord:
    """Build a :class:`LineageRecord` for a single boundary evaluation."""
    output_ref = ArtifactRef(
        path=str(artifact_path),
        sha256=hash_file(artifact_path),
    )
    return LineageRecord(
        output_artifact=output_ref,
        inputs=[],
        producer=AgentRef(
            agent_id=f"phase_gate:{phase.value}",
            run_id=task.id,
            tick_id=f"{boundary[0].value}->{boundary[1].value}",
        ),
        prompt_sha=_prompt_sha(results),
        model=phase.value,
        cost_usd=0.0,
        tokens=0,
        timestamp=time.time(),
        regulatory_class=PHASE_GATE_REGULATORY_CLASS,
    )


def make_lineage_hook(
    writer: LineageWriter,
    *,
    artifact_path_resolver: Callable[[Task, Phase], Path] | None = None,
) -> Any:
    """Return a hook usable as :attr:`PhasedRunner.gate_lineage_hook`.

    The closure captures *writer* and the optional resolver so callers
    don't have to thread the writer through the runner constructor.

    Args:
        writer: WAL-backed lineage writer for the active run.
        artifact_path_resolver: Optional callable mapping
            ``(task, phase) -> Path`` to override the default
            ``.sdd/runtime/phase_artifacts/<task_id>/<phase>.json`` lookup.
    """
    from pathlib import Path as _Path

    def _hook(
        task: Task,
        phase: Phase,
        boundary: tuple[Phase, Phase],
        results: list[GateResult],
    ) -> None:
        if artifact_path_resolver is not None:
            artifact_path = artifact_path_resolver(task, phase)
        else:
            artifact_path = _Path(".sdd/runtime/phase_artifacts") / task.id / f"{phase.value}.json"
        record = build_phase_gate_record(
            task=task,
            phase=phase,
            boundary=boundary,
            results=results,
            artifact_path=artifact_path,
        )
        writer.emit(record, actor=f"phase_gate:{phase.value}")

    return _hook


__all__ = [
    "PHASE_GATE_REGULATORY_CLASS",
    "build_phase_gate_record",
    "gate_results_summary",
    "make_lineage_hook",
]
