"""Deterministic projection from a schedule fire to a canonical task graph.

The projection is the load-bearing property of #1798: two operators with
identical ``(schedule_id, fire_time, last_state)`` MUST land on the
byte-identical task graph. Any drift breaks the reproducible-firing
contract that downstream audit walks depend on.

Discipline (do not relax without parent approval):

- The projection function is pure. No ``time.time()``, no wall-clock
  comparisons, no random shuffling, no host-dependent ordering, no
  network reads, no environment lookups.
- All inputs flow in via the function signature; all outputs flow out via
  the returned task-graph mapping.
- The canonical encoding sorts keys and freezes container order so two
  dicts that compare equal serialise to byte-identical JSON.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Schema-rev marker baked into the projection. Bumping the rev changes
#: every projection_hash and is therefore the single source of truth for
#: when the deterministic contract is allowed to evolve.
SCHEDULE_PROJECTION_REV = "1"


@dataclass(frozen=True)
class TaskNode:
    """One node of the projected task graph.

    Frozen + sortable so the canonical encoder can lay nodes out by a
    stable key regardless of how the projection iterated through state.
    """

    task_id: str
    role: str
    title: str
    description: str
    depends_on: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProjectionResult:
    """The byte-stable output of the projection function.

    Attributes:
        nodes: Tuple of task nodes ordered by ``task_id``.
        projection_hash: SHA-256 over the canonical bytes; this is the
            value an operator records in the audit chain and compares
            against a second operator's chain to prove they fired the
            byte-identical graph.
        canonical_bytes: The exact bytes hashed; surfacing them lets the
            audit-chain verifier do its own digest re-check without
            recomputing the projection.
    """

    nodes: tuple[TaskNode, ...]
    projection_hash: str
    canonical_bytes: bytes
    rev: str = SCHEDULE_PROJECTION_REV
    schedule_id: str = ""
    fire_time: int = 0
    last_state_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the projection.

        Useful for ``schedule show --json`` and for the audit-chain
        payload. The dict is built from the canonical bytes so the JSON
        round-trips identically across runs.
        """
        return json.loads(self.canonical_bytes.decode())


def _digest_last_state(last_state: Mapping[str, Any] | None) -> str:
    """Hash the ``last_state`` mapping into a stable digest.

    A None / empty mapping hashes to a fixed sentinel so the very first
    fire of a fresh schedule produces a deterministic projection without
    requiring the caller to invent a synthetic state.
    """
    if not last_state:
        return "genesis"
    canonical = json.dumps(last_state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def _canonical_nodes(nodes: list[TaskNode]) -> tuple[TaskNode, ...]:
    """Sort task nodes by id so projection output is order-independent.

    The projection callers may emit nodes in any order; the canonical
    output sorts by ``task_id``. Two operators that disagree on
    iteration order still converge on the same byte sequence.
    """
    return tuple(sorted(nodes, key=lambda n: n.task_id))


def _node_to_dict(node: TaskNode) -> dict[str, Any]:
    """Serialise a TaskNode to a sort-friendly dict.

    ``depends_on`` and ``metadata`` are sorted explicitly so a caller
    that emits ``("b", "a")`` lands on the same bytes as a caller that
    emits ``("a", "b")``.
    """
    return {
        "task_id": node.task_id,
        "role": node.role,
        "title": node.title,
        "description": node.description,
        "depends_on": sorted(node.depends_on),
        "metadata": sorted([list(item) for item in node.metadata]),
    }


def project_schedule_fire(
    *,
    schedule_id: str,
    fire_time: int,
    last_state: Mapping[str, Any] | None,
    goal: str = "",
    scenario_id: str = "",
) -> ProjectionResult:
    """Project ``(schedule_id, fire_time, last_state)`` onto a task graph.

    PURE function. No wall-clock, no randomness, no host-dependent state.

    The projection currently emits a single root task that the
    orchestrator picks up via the existing trigger pipeline. Future revs
    may emit multi-node graphs; bump ``SCHEDULE_PROJECTION_REV`` when
    that happens, because the projection_hash baked into past audit
    entries must remain reproducible against the rev that produced them.

    Args:
        schedule_id: Stable schedule identifier.
        fire_time: Unix epoch (integer seconds) of the canonical fire
            instant. We deliberately require ``int`` not ``float`` so
            sub-second drift cannot fork two operators' projections.
        last_state: Optional mapping that callers can use to fold the
            previous fire outcome into the projection. Today this is
            unused in the body but baked into the digest so the
            contract is honoured by the function signature.
        goal: Free-form goal text from the registered schedule.
        scenario_id: Optional named scenario id.

    Returns:
        A ProjectionResult with the canonical task graph and its hash.
    """
    # Runtime guard against an untyped caller handing us a float: even
    # though the type signature pins ``int``, the deterministic contract
    # is load-bearing enough that we re-check at runtime. The pyright
    # ``reportUnnecessaryIsInstance`` suppression is deliberate.
    if not isinstance(fire_time, int):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("fire_time must be an integer epoch second; floats permit cross-host drift")
    state_digest = _digest_last_state(last_state)

    # Build a single root task. The description is fully determined by
    # the inputs so two operators land on byte-identical output.
    description_lines = [
        f"Schedule {schedule_id} fire at epoch {fire_time}.",
    ]
    if goal:
        description_lines.append(f"Goal: {goal}")
    if scenario_id:
        description_lines.append(f"Scenario: {scenario_id}")
    description = "\n".join(description_lines)

    # The task_id MUST be deterministic. Derive it from the canonical
    # tuple so two operators recompute the same id.
    task_id_seed = json.dumps(
        {
            "schedule_id": schedule_id,
            "fire_time": fire_time,
            "state_digest": state_digest,
            "kind": "root",
            "rev": SCHEDULE_PROJECTION_REV,
        },
        sort_keys=True,
    ).encode()
    task_id = "sched-task-" + hashlib.sha256(task_id_seed).hexdigest()[:16]

    metadata: tuple[tuple[str, str], ...] = (
        ("schedule_id", schedule_id),
        ("fire_time", str(fire_time)),
        ("rev", SCHEDULE_PROJECTION_REV),
        ("source", "schedule"),
    )
    if scenario_id:
        metadata = (*metadata, ("scenario_id", scenario_id))

    role = "manager"
    title = f"Scheduled goal: {(goal or scenario_id or schedule_id)[:120]}"

    root = TaskNode(
        task_id=task_id,
        role=role,
        title=title,
        description=description,
        depends_on=(),
        metadata=metadata,
    )

    nodes = _canonical_nodes([root])
    canonical_obj = {
        "rev": SCHEDULE_PROJECTION_REV,
        "schedule_id": schedule_id,
        "fire_time": fire_time,
        "last_state_digest": state_digest,
        "goal": goal,
        "scenario_id": scenario_id,
        "nodes": [_node_to_dict(n) for n in nodes],
    }
    canonical_bytes = json.dumps(
        canonical_obj,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    projection_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return ProjectionResult(
        nodes=nodes,
        projection_hash=projection_hash,
        canonical_bytes=canonical_bytes,
        rev=SCHEDULE_PROJECTION_REV,
        schedule_id=schedule_id,
        fire_time=fire_time,
        last_state_digest=state_digest,
    )
