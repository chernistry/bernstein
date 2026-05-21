"""Mechanical exit-criteria gate for phase boundaries.

The original phase pipeline only enforced *shape* between phases (the
artefact has the right keys and types).  We identified the gap in our
existing pipeline: shape is not exit criteria.  An ``implement`` agent
can sail through with a ``plan`` artefact whose ``decisions`` reference
identifiers that don't appear in ``research.constraints``, an
``open_questions`` list of length > 0, or a serialised payload that
quietly grew past the per-phase byte budget.  None of that triggers a
failure today.

This module adds a small, deterministic, stdlib-only rule runner that
fires between phases.  Every rule produces a :class:`GateResult` with
PASS/PARTIAL/FAIL/SKIPPED and a machine-parseable ``repair`` hint.  On
``FAIL`` the runner re-fires the *failing* phase exactly once with the
violation list pushed into the seed ``open_questions`` field - fully
closed loop, no human in the inner loop.

Each gate result is also recorded as a ``phase_gate`` lineage event in
the existing per-artifact lineage trail
(:mod:`bernstein.core.persistence.lineage`) so audit chain becomes
per-phase, per-rule.

Rules
-----
``R001-no-open-questions``
    ``current.open_questions`` must be empty for ``plan`` and
    ``implement`` (research is allowed to leave them open).

``R002-decisions-reference-prior``
    Every ``current.decisions`` entry containing an ``<id:foo>`` marker
    must resolve against ``prior.decisions`` or ``prior.constraints``.

``R003-acyclic-decision-graph``
    ``current.decisions`` are interpreted as ``A -> B`` edges when they
    contain ``->`` or ``→``; the resulting graph must be acyclic.  The
    plan phase's explicit ``dependencies`` extra is consumed too when
    present.

``R004-monotonic-constraint-set``
    ``prior.constraints`` ⊆ ``current.constraints`` for the
    ``plan→implement`` boundary - implement may not silently drop
    constraints.

``R005-byte-budget``
    ``len(current.to_json()) ≤ spec.max_tokens * 4``.  Rough byte-budget
    guard so the artefact never re-bloats into context.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from bernstein.core.orchestration.phase_pipeline import Phase

if TYPE_CHECKING:
    from bernstein.core.orchestration.phase_pipeline import PhaseArtifact, PhaseSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome / result model
# ---------------------------------------------------------------------------


class GateOutcome(StrEnum):
    """Per-rule outcome.  ``PARTIAL`` is treated as ``PASS`` in v1 but logged."""

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class GateResult:
    """Result of evaluating a single :class:`PhaseGateRule`.

    Attributes:
        rule_id: Stable rule identifier (``"R001-no-open-questions"``).
        label: Human-readable rule name.
        outcome: Pass / partial / fail / skipped.
        repair: Machine-parseable hint that gets seeded into the next
            invocation's ``open_questions`` list when retrying.  Empty
            string when the outcome was PASS or SKIPPED.
        details: Free-form details accumulated during the check; useful
            for the lineage event payload.
        boundary_from: Phase whose artefact was the *prior* input.
        boundary_to: Phase whose artefact was the *current* output.
    """

    rule_id: str
    label: str
    outcome: GateOutcome
    repair: str = ""
    details: str = ""
    boundary_from: Phase | None = None
    boundary_to: Phase | None = None

    @property
    def passed(self) -> bool:
        """``True`` for PASS/PARTIAL/SKIPPED - only FAIL blocks the boundary."""
        return self.outcome is not GateOutcome.FAIL


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


RuleFn = Callable[["PhaseArtifact", "PhaseArtifact", "PhaseSpec"], GateResult]
"""Signature for a rule callable.

The first argument is the *prior* artefact (or a synthetic empty one for
the first boundary), the second is the *current* artefact under
evaluation, the third is the current phase's spec (the ``max_tokens``
soft cap drives R005).
"""


@dataclass(frozen=True)
class PhaseGateRule:
    """Registered rule entry.

    Attributes:
        rule_id: Stable rule identifier.
        label: Human-readable name.
        fn: Pure function returning a :class:`GateResult`.
        applies_to: Set of ``(from_phase, to_phase)`` boundaries the rule
            fires on.  Empty set means "every boundary" (e.g. the byte
            budget rule).
    """

    rule_id: str
    label: str
    fn: RuleFn
    applies_to: frozenset[tuple[Phase, Phase]] = field(default_factory=frozenset)


_RULES: dict[str, PhaseGateRule] = {}


def register_rule(
    rule_id: str,
    label: str,
    *,
    applies_to: frozenset[tuple[Phase, Phase]] | None = None,
) -> Callable[[RuleFn], RuleFn]:
    """Decorator: register *fn* under *rule_id*.

    Args:
        rule_id: Stable identifier (``"R001-no-open-questions"``).
        label: Human-readable display name.
        applies_to: Boundaries the rule fires on; ``None`` means every boundary.
    """

    def _decorator(fn: RuleFn) -> RuleFn:
        _RULES[rule_id] = PhaseGateRule(
            rule_id=rule_id,
            label=label,
            fn=fn,
            applies_to=applies_to or frozenset(),
        )
        return fn

    return _decorator


def list_rules() -> list[PhaseGateRule]:
    """Return registered rules in insertion order."""
    return list(_RULES.values())


def get_rule(rule_id: str) -> PhaseGateRule | None:
    """Look up a rule by id; useful for plan-YAML allowlist/denylist resolution."""
    return _RULES.get(rule_id)


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------


_R001_BOUNDARIES = frozenset({(Phase.RESEARCH, Phase.PLAN), (Phase.PLAN, Phase.IMPLEMENT)})


@register_rule(
    "R001-no-open-questions",
    "no-open-questions",
    applies_to=_R001_BOUNDARIES,
)
def _r001(prior: PhaseArtifact, current: PhaseArtifact, _spec: PhaseSpec) -> GateResult:
    """Plan and implement must have an empty ``open_questions`` list.

    Research is allowed to leave them open - that's how research works.
    """
    del prior  # unused
    if not current.open_questions:
        return GateResult(
            rule_id="R001-no-open-questions",
            label="no-open-questions",
            outcome=GateOutcome.PASS,
        )
    repair = (
        "open_questions must be resolved before this phase is accepted. "
        f"unresolved: {'; '.join(current.open_questions)}"
    )
    return GateResult(
        rule_id="R001-no-open-questions",
        label="no-open-questions",
        outcome=GateOutcome.FAIL,
        repair=repair,
        details=f"{len(current.open_questions)} unresolved questions",
    )


_ID_MARKER_RE = re.compile(r"<id:([^>]+)>")


@register_rule(
    "R002-decisions-reference-prior",
    "decisions-reference-prior",
)
def _r002(prior: PhaseArtifact, current: PhaseArtifact, _spec: PhaseSpec) -> GateResult:
    """Every ``<id:foo>`` marker must resolve against the prior artefact."""
    known: set[str] = set()
    for entry in (*prior.decisions, *prior.constraints):
        known.update(_ID_MARKER_RE.findall(entry))
        # The plain text after a ``<id:foo>`` marker is also addressable.
        # Tokens without markers are addressable by exact-string match -
        # rare, but we want decisions like ``"python 3.12"`` to count.
        if entry not in known:
            known.add(entry)

    unresolved: list[str] = []
    for entry in current.decisions:
        for marker in _ID_MARKER_RE.findall(entry):
            if marker not in known:
                unresolved.append(marker)

    if not unresolved:
        return GateResult(
            rule_id="R002-decisions-reference-prior",
            label="decisions-reference-prior",
            outcome=GateOutcome.PASS,
        )
    return GateResult(
        rule_id="R002-decisions-reference-prior",
        label="decisions-reference-prior",
        outcome=GateOutcome.FAIL,
        repair=(
            "decisions reference identifiers that do not appear in the prior "
            f"artefact: {', '.join(sorted(set(unresolved)))}"
        ),
        details=f"{len(unresolved)} unresolved markers",
    )


_EDGE_RE = re.compile(r"\s*(?:->|→)\s*")


def _extract_edges(items: list[str]) -> list[tuple[str, str]]:
    """Parse ``A -> B`` style entries into ``(A, B)`` edges.

    Entries without an arrow are ignored (they are plain decisions, not
    dependency edges).  Whitespace around the arrow is forgiving so
    handcrafted plan artefacts don't fail R003 for cosmetic reasons.
    """
    edges: list[tuple[str, str]] = []
    for entry in items:
        if "->" not in entry and "→" not in entry:
            continue
        parts = _EDGE_RE.split(entry, maxsplit=1)
        if len(parts) != 2:
            continue
        a, b = parts[0].strip(), parts[1].strip()
        if a and b:
            edges.append((a, b))
    return edges


def _has_cycle(edges: list[tuple[str, str]]) -> tuple[bool, list[str]]:
    """Return ``(has_cycle, cycle_nodes)`` via iterative DFS.

    The implementation is iterative rather than recursive so a long
    dependency chain doesn't blow the Python recursion limit.
    """
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, [])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in adj}

    for start in list(adj.keys()):
        if color[start] != WHITE:
            continue
        # Stack of (node, iterator over neighbours)
        stack: list[tuple[str, list[str]]] = [(start, adj[start].copy())]
        color[start] = GRAY
        path: list[str] = [start]
        while stack:
            node, neighbours = stack[-1]
            if not neighbours:
                color[node] = BLACK
                stack.pop()
                if path and path[-1] == node:
                    path.pop()
                continue
            nxt = neighbours.pop()
            if color.get(nxt, WHITE) == GRAY:
                # Cycle detected; trim path to the first occurrence of nxt.
                idx = path.index(nxt) if nxt in path else 0
                return True, [*path[idx:], nxt]
            if color.get(nxt, WHITE) == WHITE:
                color[nxt] = GRAY
                path.append(nxt)
                stack.append((nxt, list(adj.get(nxt, []))))
    return False, []


@register_rule(
    "R003-acyclic-decision-graph",
    "acyclic-decision-graph",
)
def _r003(_prior: PhaseArtifact, current: PhaseArtifact, _spec: PhaseSpec) -> GateResult:
    """The decision/dependency graph must be acyclic."""
    edges = _extract_edges(current.decisions)
    extras_deps = current.extras.get("dependencies") if current.extras else None
    if isinstance(extras_deps, list):
        edges.extend(_extract_edges([str(item) for item in extras_deps]))

    if not edges:
        return GateResult(
            rule_id="R003-acyclic-decision-graph",
            label="acyclic-decision-graph",
            outcome=GateOutcome.SKIPPED,
            details="no dependency edges declared",
        )

    has_cycle, cycle = _has_cycle(edges)
    if not has_cycle:
        return GateResult(
            rule_id="R003-acyclic-decision-graph",
            label="acyclic-decision-graph",
            outcome=GateOutcome.PASS,
            details=f"{len(edges)} edges, no cycle",
        )
    return GateResult(
        rule_id="R003-acyclic-decision-graph",
        label="acyclic-decision-graph",
        outcome=GateOutcome.FAIL,
        repair=(f"dependency graph contains a cycle: {' -> '.join(cycle)}. Break the cycle before re-emitting."),
        details=f"cycle of length {len(cycle)}",
    )


@register_rule(
    "R004-monotonic-constraint-set",
    "monotonic-constraint-set",
    applies_to=frozenset({(Phase.PLAN, Phase.IMPLEMENT)}),
)
def _r004(prior: PhaseArtifact, current: PhaseArtifact, _spec: PhaseSpec) -> GateResult:
    """``prior.constraints`` must be a subset of ``current.constraints``."""
    prior_set = set(prior.constraints)
    current_set = set(current.constraints)
    dropped = sorted(prior_set - current_set)
    if not dropped:
        return GateResult(
            rule_id="R004-monotonic-constraint-set",
            label="monotonic-constraint-set",
            outcome=GateOutcome.PASS,
        )
    return GateResult(
        rule_id="R004-monotonic-constraint-set",
        label="monotonic-constraint-set",
        outcome=GateOutcome.FAIL,
        repair=(
            "implement phase silently dropped constraints from the plan: "
            f"{', '.join(dropped)}. Re-emit with these constraints carried forward."
        ),
        details=f"{len(dropped)} dropped constraint(s)",
    )


@register_rule(
    "R005-byte-budget",
    "byte-budget",
)
def _r005(_prior: PhaseArtifact, current: PhaseArtifact, spec: PhaseSpec) -> GateResult:
    """Serialised artefact must fit within ``spec.max_tokens * 4`` bytes.

    Four bytes per token is the conventional rough upper bound; phases
    that overshoot have almost certainly leaked transcript material into
    the structured artefact and should be re-fired (or hard-failed; see
    ``PHASE_PIPELINE.gate_byte_budget_hard_fail``).
    """
    payload_bytes = len(current.to_json().encode("utf-8"))
    budget = spec.max_tokens * 4
    if payload_bytes <= budget:
        return GateResult(
            rule_id="R005-byte-budget",
            label="byte-budget",
            outcome=GateOutcome.PASS,
            details=f"{payload_bytes} bytes (budget {budget})",
        )
    return GateResult(
        rule_id="R005-byte-budget",
        label="byte-budget",
        outcome=GateOutcome.FAIL,
        repair=(
            f"artefact is {payload_bytes} bytes, exceeds budget of {budget}. "
            "Compress the summary; the next phase only sees this artefact, "
            "not the raw transcript."
        ),
        details=f"{payload_bytes} bytes (budget {budget})",
    )


# ---------------------------------------------------------------------------
# Boundary runner
# ---------------------------------------------------------------------------


_DENY_PREFIX = "-"


def _select_rules(
    boundary: tuple[Phase, Phase],
    *,
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
) -> list[PhaseGateRule]:
    """Return the rules that apply at *boundary* under the allow/deny lists."""
    rules: list[PhaseGateRule] = []
    for rule in _RULES.values():
        if rule.applies_to and boundary not in rule.applies_to:
            continue
        if denied and rule.rule_id in denied:
            continue
        if allowed and rule.rule_id not in allowed:
            continue
        rules.append(rule)
    return rules


def parse_rule_filter(items: list[str] | None) -> tuple[list[str], list[str]]:
    """Split a ``phase_gates: [-R005, R001]`` list into (allow, deny).

    Empty / ``None`` input returns ``([], [])`` - no filter applied.
    """
    allowed: list[str] = []
    denied: list[str] = []
    if not items:
        return allowed, denied
    for raw in items:
        token = raw.strip()
        if not token:
            continue
        if token.startswith(_DENY_PREFIX):
            denied.append(token.removeprefix(_DENY_PREFIX))
        else:
            allowed.append(token)
    return allowed, denied


def evaluate_boundary(
    *,
    prior: PhaseArtifact | None,
    current: PhaseArtifact,
    boundary: tuple[Phase, Phase],
    spec: PhaseSpec,
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
) -> list[GateResult]:
    """Run every applicable rule at *boundary*; return one result per rule.

    Args:
        prior: The previous phase's artefact, or ``None`` for the very
            first boundary (research entry).  The synthetic empty
            artefact substituted in that case is documented behaviour:
            R001 still fires because research is exempt by ``applies_to``.
        current: Artefact under evaluation.
        boundary: ``(from_phase, to_phase)`` tuple.
        spec: Spec for the *current* phase (drives R005 budget).
        allowed: Optional allowlist of ``rule_id`` strings.
        denied: Optional denylist (entries removed from the run).
    """
    if prior is None:
        # Importing here keeps this module independent of the heavy
        # PhaseArtifact import path at top-level (tests use both).
        from bernstein.core.orchestration.phase_pipeline import PhaseArtifact as _PA

        prior = _PA(summary="<initial>", decisions=[], constraints=[], open_questions=[])

    rules = _select_rules(boundary, allowed=allowed, denied=denied)
    results: list[GateResult] = []
    for rule in rules:
        try:
            res = rule.fn(prior, current, spec)
        except Exception:
            logger.exception("phase gate %s raised; treating as FAIL", rule.rule_id)
            res = GateResult(
                rule_id=rule.rule_id,
                label=rule.label,
                outcome=GateOutcome.FAIL,
                repair=f"rule {rule.rule_id} raised an exception during evaluation",
            )
        results.append(
            GateResult(
                rule_id=res.rule_id,
                label=res.label,
                outcome=res.outcome,
                repair=res.repair,
                details=res.details,
                boundary_from=boundary[0],
                boundary_to=boundary[1],
            )
        )
    return results


def collect_failures(results: list[GateResult]) -> list[GateResult]:
    """Filter *results* down to outright failures (``GateOutcome.FAIL``)."""
    return [r for r in results if r.outcome is GateOutcome.FAIL]


def violations_to_open_questions(failures: list[GateResult]) -> list[str]:
    """Render *failures* as seed ``open_questions`` entries for the re-fire.

    Each entry is ``"<rule_id>: <repair>"`` so the next agent invocation
    sees both the structural diagnosis and the targeted fix.  The rule
    ids stay in the seed so a second-round failure on the same rule is
    auditable as a true repeat, not a fresh issue.
    """
    out: list[str] = []
    for f in failures:
        repair = f.repair.strip() or f.label
        out.append(f"{f.rule_id}: {repair}")
    return out


__all__ = [
    "GateOutcome",
    "GateResult",
    "PhaseGateRule",
    "collect_failures",
    "evaluate_boundary",
    "get_rule",
    "list_rules",
    "parse_rule_filter",
    "register_rule",
    "violations_to_open_questions",
]
