"""Iterative self-refinement loop with per-round critique and gates.

Bernstein's :mod:`bernstein.core.orchestration.best_of_n` covers
parallel candidate generation where a judge picks the winner.  This
module covers the *complementary* shape: one candidate iteratively
improved across N rounds with the previous round's critique fed back
as the next round's prompt prefix.

The runner is opt-in: a :class:`bernstein.core.tasks.models.Task` opts
in by setting ``refinement_rounds`` in ``[2, MAX_REFINEMENT_ROUNDS]``.
Unset leaves the existing single-shot pipeline untouched.

Stop conditions (any one ends the loop early):

* **rounds**: the configured ``rounds`` budget is exhausted.
* **plateau**: the critic's score fails to improve for two consecutive
  rounds (oscillation guard).
* **threshold**: the critic's score reaches the configured
  ``score_threshold``.
* **gate**: a between-rounds gate pipeline run fails.
* **budget**: cumulative cost exceeds the task's spend cap; uses
  :func:`bernstein.core.cost.budget_actions.BudgetPolicy.evaluate`.
* **adversary_veto**: the critic returned ``Critique(veto=True)``.

The runner is deliberately adapter-agnostic.  Wire concrete
``drafter``, ``refiner``, and ``critic`` callbacks at the call site  -
see :class:`RefinementLoopRunner` for the contract.  Every round emits
a decision-log record under
:data:`bernstein.core.observability.decision_log.VALID_KINDS` (kind
``gate_fire``  -  gates the loop just as a quality gate would) and a
calibration log entry so operators can audit refinement quality over
time.

CLI: ``bernstein run plan.yaml --refine 'rounds:3,critic:adversary,stop:plateau'``.
The parser :func:`parse_refine_spec` accepts the comma-separated form;
malformed entries raise :class:`ValueError`.

Mutual-exclusion with best-of-N: a task cannot set both
``best_of_n`` and ``refinement_rounds`` simultaneously.  The runner
asserts the invariant in :meth:`RefinementLoopRunner.run`.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from bernstein.core.orchestration.refinement_schemas import (
    Critique,
    clamp_score,
)

if TYPE_CHECKING:
    from bernstein.core.tasks.models import Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_REFINEMENT_ROUNDS = 2
"""Minimum rounds when refinement is opted in; below this we collapse to
the single-shot path so the loop is never a no-op."""

MAX_REFINEMENT_ROUNDS = 6
"""Maximum rounds.  Above this the schedule is dominated by accumulated
context cost without measurable quality gains; the cap is enforced at
parse and run time."""

DEFAULT_SCORE_THRESHOLD = 0.95
"""Score at or above which the loop stops early (artefact considered
done).  Operators can override per call."""

PLATEAU_WINDOW = 2
"""Number of *consecutive* non-improving rounds that trigger the
plateau early-stop.  Two is enough to distinguish noise from true
plateau without over-spending on a flat curve."""

EarlyStopReason = Literal[
    "rounds",
    "plateau",
    "threshold",
    "gate",
    "budget",
    "adversary_veto",
]


# ---------------------------------------------------------------------------
# Callback signatures
# ---------------------------------------------------------------------------

Drafter = Callable[["Task"], "RoundArtefact"]
"""Produce the round-1 draft for a task.  Returns the artefact and its
round cost in :class:`RoundArtefact`."""

Refiner = Callable[["Task", "RoundArtefact", Critique], "RoundArtefact"]
"""Produce a refined artefact given the prior artefact and critique.

Implementations typically echo ``critique.rationale`` into the next
prompt prefix and call the same underlying agent the drafter used."""

Critic = Callable[["Task", "RoundArtefact", int], Critique]
"""Score a candidate artefact for round *round_index* (1-based).

The critic is expected to return a :class:`Critique` with a
``score`` in ``[0.0, 1.0]``.  Implementations typically call the cheap
tier of the LLM cascade or the bundled ``adversary`` role; the runner
never inspects ``rationale`` text."""

GateRunner = Callable[["Task", "RoundArtefact", int], bool]
"""Run the configured gate pipeline.  Return ``True`` for pass,
``False`` to halt the loop with ``early_stop_reason="gate"``.  The
default wiring delegates to :func:`bernstein.core.quality.gate_pipeline`
helpers; tests provide simple stubs."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundArtefact:
    """One round's output from the drafter or refiner.

    Attributes:
        content: Free-form artefact body (e.g. diff text, prose, JSON).
        cost_usd: Round cost in USD as reported by the adapter.  The
            runner sums these into ``RefinementReport.per_round_cost``
            and feeds the cumulative total into the budget policy.
        metadata: Optional adapter-side metadata.  Persisted verbatim.
    """

    content: str
    cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class RefinementReport:
    """End-to-end outcome of one refinement loop run.

    Attributes:
        rounds_run: How many rounds actually executed (including the
            round that produced ``final_artefact``).
        final_artefact: The artefact from the last completed round; the
            caller is responsible for committing or discarding it.
        per_round_critique: Critiques produced *after* each round.  The
            ith entry critiques the ith round's artefact (1-indexed).
        per_round_cost: USD cost reported per round, in order.
        per_round_quality_score: Critic score reported per round.
        early_stop_reason: One of :data:`EarlyStopReason` values.
            ``"rounds"`` means the full budget was used without an
            early-stop trigger.
        gate_failed_round: 1-based round at which the gate halted the
            loop; ``None`` when ``early_stop_reason != "gate"``.
        cumulative_cost_usd: Sum of ``per_round_cost``.
    """

    rounds_run: int
    final_artefact: RoundArtefact
    per_round_critique: list[Critique]
    per_round_cost: list[float]
    per_round_quality_score: list[float]
    early_stop_reason: EarlyStopReason
    gate_failed_round: int | None
    cumulative_cost_usd: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clamp_rounds(requested: int) -> int:
    """Clamp *requested* into ``[1, MAX_REFINEMENT_ROUNDS]``.

    A value below :data:`MIN_REFINEMENT_ROUNDS` collapses to ``1``  -
    the legacy single-shot path  -  so the loop is never invoked for a
    no-op count.
    """
    if requested < MIN_REFINEMENT_ROUNDS:
        return 1
    if requested > MAX_REFINEMENT_ROUNDS:
        return MAX_REFINEMENT_ROUNDS
    return int(requested)


def is_refinement(task: Task) -> bool:
    """Return ``True`` when *task* opts into the refinement loop.

    Honours :attr:`bernstein.core.tasks.models.Task.refinement_rounds`;
    a value of ``None`` or below the minimum keeps the task on the
    legacy single-shot path.
    """
    n = getattr(task, "refinement_rounds", None)
    if n is None:
        return False
    try:
        return int(n) >= MIN_REFINEMENT_ROUNDS
    except (TypeError, ValueError):
        return False


def task_rounds(task: Task) -> int:
    """Return the configured round count clamped into the valid range.

    Returns ``1`` for tasks not opted into refinement so callers can
    treat the value as "agent invocations to perform" without a branch.
    """
    if not is_refinement(task):
        return 1
    raw = getattr(task, "refinement_rounds", None)
    try:
        return clamp_rounds(int(raw or 1))
    except (TypeError, ValueError):
        return 1


def detect_plateau(scores: list[float], window: int = PLATEAU_WINDOW) -> bool:
    """Return ``True`` when the last *window* rounds failed to improve.

    "Failed to improve" means each of the last ``window`` scores is at
    most the score before them by the strict-equality test ``<=``.
    Strict ``<=`` rather than ``<`` catches sticky-zero critics that
    return identical scores between rounds  -  an oscillation guard
    needs to fire on flat lines, not just on declines.

    Args:
        scores: Per-round quality scores in execution order.
        window: How many trailing rounds must fail to improve.

    Returns:
        ``True`` when a plateau is detected.  ``False`` when the curve
        is still climbing or when there are not yet enough rounds to
        evaluate.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    if len(scores) < window + 1:
        return False
    pivot = scores[-window - 1]
    return all(score <= pivot for score in scores[-window:])


def parse_refine_spec(spec: str) -> RefineSpec:
    """Parse a CLI ``--refine`` value into a :class:`RefineSpec`.

    The spec format is a comma-separated key:value list, e.g.
    ``rounds:3,critic:adversary,stop:plateau``.  Unknown keys raise
    :class:`ValueError` so typos surface immediately instead of being
    silently ignored.

    Recognised keys:

    * ``rounds``: integer in ``[MIN_REFINEMENT_ROUNDS, MAX_REFINEMENT_ROUNDS]``.
    * ``critic``: free-form role name (e.g. ``adversary``, ``reviewer``).
    * ``stop``: one of ``plateau``, ``threshold``, ``rounds``.
    * ``threshold``: float in ``[0.0, 1.0]`` for the score gate.

    Args:
        spec: Raw CLI string.

    Returns:
        A populated :class:`RefineSpec`.

    Raises:
        ValueError: Malformed spec or unknown key.
    """
    if not spec or not spec.strip():
        raise ValueError("refine spec must be a non-empty string")
    rounds = MIN_REFINEMENT_ROUNDS
    critic = "adversary"
    stop = "rounds"
    threshold = DEFAULT_SCORE_THRESHOLD
    for raw_entry in spec.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(f"refine spec entry missing ':' separator: {entry!r}")
        key, _, value = entry.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "rounds":
            try:
                rounds = int(value)
            except ValueError as exc:
                raise ValueError(f"refine.rounds must be an integer: {value!r}") from exc
            if rounds < MIN_REFINEMENT_ROUNDS or rounds > MAX_REFINEMENT_ROUNDS:
                raise ValueError(f"refine.rounds {rounds} outside [{MIN_REFINEMENT_ROUNDS},{MAX_REFINEMENT_ROUNDS}]")
        elif key == "critic":
            if not value:
                raise ValueError("refine.critic must be non-empty")
            critic = value
        elif key == "stop":
            if value not in {"plateau", "threshold", "rounds"}:
                raise ValueError(f"refine.stop must be plateau|threshold|rounds, got {value!r}")
            stop = value
        elif key == "threshold":
            try:
                threshold = float(value)
            except ValueError as exc:
                raise ValueError(f"refine.threshold must be a float: {value!r}") from exc
            if threshold < 0.0 or threshold > 1.0:
                raise ValueError(f"refine.threshold {threshold} outside [0.0, 1.0]")
        else:
            raise ValueError(f"unknown refine key: {key!r}")
    return RefineSpec(
        rounds=rounds,
        critic=critic,
        stop=stop,
        score_threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefineSpec:
    """Parsed CLI refinement options.

    Attributes:
        rounds: Total round budget, ``[MIN_REFINEMENT_ROUNDS,
            MAX_REFINEMENT_ROUNDS]``.
        critic: Critic role identifier  -  agent-side string consumed by
            the wiring layer, opaque to the runner.
        stop: Operator-asserted preferred stop reason; the runner
            still honours all early-stop conditions, but ``"threshold"``
            and ``"plateau"`` lift the corresponding gate to "active".
        score_threshold: 0.0..1.0 threshold consulted when
            ``stop == "threshold"``.
    """

    rounds: int
    critic: str
    stop: str
    score_threshold: float


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class RefinementLoopRunner:
    """Drive a task through ``rounds`` rounds of critique+refine.

    Attributes:
        drafter: Round-1 generator.  Required.
        refiner: Rounds 2..N generator that consumes the prior critique.
            Required.
        critic: Critic callback invoked after every round.  Required.
        gate_runner: Optional gate pipeline runner.  ``None`` skips the
            between-rounds gate phase entirely.
        budget_usd: Optional cumulative cost cap.  ``None`` disables
            the budget circuit breaker.
        score_threshold: Early-stop threshold for the critic score.
        plateau_window: Override the plateau-window heuristic; usually
            left at :data:`PLATEAU_WINDOW`.
        seed: Optional RNG seed used by the runner for any internal
            tie-breaks; surfaced so operators can reproduce a run.
    """

    drafter: Drafter
    refiner: Refiner
    critic: Critic
    gate_runner: GateRunner | None = None
    budget_usd: float | None = None
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    plateau_window: int = PLATEAU_WINDOW
    seed: int | None = None

    def run(self, task: Task) -> RefinementReport:
        """Execute the refinement loop for *task*.

        The loop runs at most ``task.refinement_rounds`` rounds and
        stops as soon as any early-stop condition fires.  See the
        module docstring for the full stop-condition list.

        Raises:
            ValueError: When ``task.refinement_rounds`` and
                ``task.best_of_n`` are both set, or when
                ``task.refinement_rounds`` is below the minimum.
        """
        _assert_mutual_exclusion(task)
        rounds = task_rounds(task)
        if rounds < MIN_REFINEMENT_ROUNDS:
            raise ValueError(f"refinement_rounds must be >= {MIN_REFINEMENT_ROUNDS}; got {rounds}")
        if self.plateau_window < 1:
            raise ValueError("plateau_window must be >= 1")
        if self.seed is not None:
            # Seed our local RNG  -  the runner exposes ``_rng`` so adapters
            # that need a reproducible tie-break can plug in.  We do not
            # touch the global ``random.seed`` so concurrent runners stay
            # independent.
            self._rng = random.Random(self.seed)
        else:
            self._rng = random.Random()

        critiques: list[Critique] = []
        costs: list[float] = []
        scores: list[float] = []
        current = self.drafter(task)
        last_round = 0
        early_stop: EarlyStopReason = "rounds"
        gate_failed_round: int | None = None

        for round_index in range(1, rounds + 1):
            last_round = round_index
            costs.append(max(0.0, float(current.cost_usd)))
            critique = self.critic(task, current, round_index)
            critique = _normalise_critique(critique)
            critiques.append(critique)
            scores.append(critique.score)

            if critique.veto:
                early_stop = "adversary_veto"
                break

            if self.gate_runner is not None:
                gate_pass = bool(self.gate_runner(task, current, round_index))
                if not gate_pass:
                    early_stop = "gate"
                    gate_failed_round = round_index
                    break

            if critique.score >= self.score_threshold:
                early_stop = "threshold"
                break

            cumulative = sum(costs)
            if self.budget_usd is not None and self.budget_usd > 0 and cumulative >= self.budget_usd:
                early_stop = "budget"
                break

            if detect_plateau(scores, self.plateau_window):
                early_stop = "plateau"
                break

            if round_index >= rounds:
                early_stop = "rounds"
                break

            # Roll into the next round.
            current = self.refiner(task, current, critique)

        report = RefinementReport(
            rounds_run=last_round,
            final_artefact=current,
            per_round_critique=critiques,
            per_round_cost=costs,
            per_round_quality_score=scores,
            early_stop_reason=early_stop,
            gate_failed_round=gate_failed_round,
            cumulative_cost_usd=sum(costs),
        )
        _record_decision(task, report)
        _record_calibration(task, report)
        _record_metrics(task, report)
        logger.info(
            "refinement loop for task %s: rounds=%d stop=%s final_score=%.3f cost=%.4f",
            getattr(task, "id", "?"),
            report.rounds_run,
            report.early_stop_reason,
            scores[-1] if scores else 0.0,
            report.cumulative_cost_usd,
        )
        return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_mutual_exclusion(task: Task) -> None:
    """Raise :class:`ValueError` when both refinement and best-of-N are set."""
    refinement = getattr(task, "refinement_rounds", None)
    best_of_n = getattr(task, "best_of_n", None)
    refinement_on = refinement is not None and int(refinement or 0) >= MIN_REFINEMENT_ROUNDS
    best_on = best_of_n is not None and int(best_of_n or 0) >= 2
    if refinement_on and best_on:
        raise ValueError("Task.refinement_rounds and Task.best_of_n are mutually exclusive; set exactly one")


def _normalise_critique(critique: Critique) -> Critique:
    """Clamp the critic-supplied score into ``[0.0, 1.0]``.

    A buggy critic that returns ``score=2.5`` should not be allowed to
    permanently disable the threshold gate; clamping keeps the score
    field a meaningful probability.
    """
    clamped = clamp_score(critique.score)
    if clamped == critique.score:
        return critique
    return Critique(
        score=clamped,
        issues=list(critique.issues),
        veto=critique.veto,
        rationale=critique.rationale,
    )


def _record_decision(task: Task, report: RefinementReport) -> None:
    """Persist a decision-log entry summarising the loop outcome.

    Best-effort: the decision log writer may be disabled by the
    operator (``BERNSTEIN_DECISION_LOG=0``) or import-failed in tests;
    either case is a silent skip so the runner stays loosely coupled.
    """
    try:
        from bernstein.core.observability import decision_log as _dl
    except Exception:  # pragma: no cover - observability is best-effort
        return
    try:
        scores = report.per_round_quality_score
        winner_score = scores[-1] if scores else 0.0
        _dl.record_decision(
            kind="gate_fire",
            chosen=f"refinement:{report.early_stop_reason}",
            rationale=(
                f"refinement loop rounds={report.rounds_run} "
                f"stop={report.early_stop_reason} cost={report.cumulative_cost_usd:.4f}"
            ),
            confidence=clamp_score(winner_score),
            winner_score=float(winner_score),
            policy_path=("refinement_loop",),
            inputs={
                "task_id": getattr(task, "id", ""),
                "rounds_run": report.rounds_run,
                "early_stop_reason": report.early_stop_reason,
                "cumulative_cost_usd": report.cumulative_cost_usd,
            },
        )
    except Exception:  # pragma: no cover - log writes must never fail the round
        logger.debug("refinement loop decision-log emit failed", exc_info=True)


def _record_calibration(task: Task, report: RefinementReport) -> None:
    """Persist a calibration log entry per round.

    Each round's critic score is the predicted probability; the
    observed outcome is ``True`` iff the loop terminated without a
    gate failure or budget breach.
    """
    try:
        from bernstein.eval import calibration as _cal
    except Exception:  # pragma: no cover
        return
    healthy = report.early_stop_reason not in {"gate", "budget"}
    role = str(getattr(task, "role", "") or "refinement")
    for idx, score in enumerate(report.per_round_quality_score, start=1):
        try:
            _cal.log_decision(
                decision_kind="refinement_round",
                policy_path=f"refinement_loop/{role}",
                predicted_prob=clamp_score(score),
                observed_outcome=healthy and idx == len(report.per_round_quality_score),
                metadata={
                    "task_id": getattr(task, "id", ""),
                    "round": idx,
                    "early_stop_reason": report.early_stop_reason,
                },
            )
        except Exception:  # pragma: no cover - calibration is best-effort
            logger.debug("refinement loop calibration emit failed", exc_info=True)


def _record_metrics(task: Task, report: RefinementReport) -> None:
    """Emit Prometheus telemetry for one refinement run.

    Mirrors the lazy-import shape used by
    :mod:`bernstein.core.orchestration.best_of_n` so the module stays
    usable in test environments without ``prometheus_client``.
    """
    try:
        from bernstein.core.observability import prometheus as _p
    except Exception:  # pragma: no cover - metrics are advisory
        return
    counter = getattr(_p, "refinement_rounds_total", None)
    histogram = getattr(_p, "refinement_score", None)
    role = str(getattr(task, "role", "") or "")
    if counter is not None:
        try:
            counter.labels(reason=report.early_stop_reason, role=role).inc(report.rounds_run)
        except Exception:  # pragma: no cover
            logger.debug("refinement counter emit failed", exc_info=True)
    if histogram is not None:
        for score in report.per_round_quality_score:
            try:
                histogram.labels(role=role).observe(score)
            except Exception:  # pragma: no cover
                logger.debug("refinement histogram emit failed", exc_info=True)


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "MAX_REFINEMENT_ROUNDS",
    "MIN_REFINEMENT_ROUNDS",
    "PLATEAU_WINDOW",
    "Critic",
    "Drafter",
    "EarlyStopReason",
    "GateRunner",
    "RefineSpec",
    "RefinementLoopRunner",
    "RefinementReport",
    "Refiner",
    "RoundArtefact",
    "clamp_rounds",
    "detect_plateau",
    "is_refinement",
    "parse_refine_spec",
    "task_rounds",
]
