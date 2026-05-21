"""Self-driving CI escalation ladder for the autofix daemon.

This module scopes the smallest end-to-end ladder discussed in RFC
#1415. The ladder maps a normalised :class:`CIFailure` to one of four
rungs, ordered from cheapest to most invasive:

* Rung 0 - lint/format drift fixable by ``ruff check --fix --diff``.
  The actor applies the patch in-place, no model spend.
* Rung 1 - single-file test failure with a small diff. The actor spawns
  a ``ci-fixer`` agent for one round and posts the diff for operator
  review. Stubbed in this MVP - detector wired, actor returns
  ``stubbed`` so a follow-up PR can connect to the real spawn path.
* Rung 2 - multi-file failure on files the PR touched. The actor spawns
  ``ci-fixer`` plus ``qa`` for one round each and requires operator
  approval. Stubbed in this MVP - detector wired, actor returns
  ``stubbed``.
* Rung 3 - failure on a file the PR did NOT touch. The actor stops and
  posts an "out of scope - human" comment. No spawn, no spend.

The daemon picks the *lowest* matching rung. The ladder refuses to fire
a rung whose ``cost_cap_usd`` exceeds the operator-configured
``autofix.cost_cap_per_pr`` in ``bernstein.yaml``. Each fire writes a
lifecycle event into the existing autofix audit chain so an operator
can audit-replay later.

The whole subsystem is feature-flagged off by default
(``autofix.ladder.enabled = false`` in ``bernstein.yaml``). The
detector decisions are static in MVP - no learned heuristics, no
cross-PR memory. Follow-up PRs wire Rung 1 and Rung 2 actors and add
the cross-PR signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature-flag / cap loader
# ---------------------------------------------------------------------------

#: Default operator-facing cost cap when ``bernstein.yaml`` does not
#: declare one. Matches the placeholder value seeded in the repo's
#: top-level ``bernstein.yaml``.
DEFAULT_COST_CAP_PER_PR_USD: Final[float] = 1.0


@dataclass(frozen=True)
class LadderSettings:
    """Effective ladder configuration loaded from ``bernstein.yaml``.

    Attributes:
        enabled: ``True`` when ``autofix.ladder.enabled`` is truthy in
            the project ``bernstein.yaml``. The daemon refuses to fire
            any rung when ``enabled`` is ``False`` (operator opt-in).
        cost_cap_per_pr_usd: Operator-configured hard cap. Zero means
            "unlimited" - matches :class:`RepoConfig` semantics.
    """

    enabled: bool = False
    cost_cap_per_pr_usd: float = DEFAULT_COST_CAP_PER_PR_USD


def load_ladder_settings(yaml_path: Path | None = None) -> LadderSettings:
    """Return the effective ladder settings from ``bernstein.yaml``.

    The loader is intentionally lenient: missing file, missing
    ``autofix`` block, or malformed types fall back to the
    operator-flagged-off default. The lineage v1 yaml schema is heavy
    so this reader only touches the keys it needs.

    Args:
        yaml_path: Optional explicit path. Defaults to
            ``./bernstein.yaml`` from the current working directory.

    Returns:
        A populated :class:`LadderSettings`.
    """
    target = yaml_path if yaml_path is not None else Path.cwd() / "bernstein.yaml"
    if not target.exists():
        return LadderSettings()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - PyYAML is a runtime dep
        return LadderSettings()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("ladder: failed to parse %s", target)
        return LadderSettings()
    if not isinstance(raw, dict):
        return LadderSettings()
    autofix_block = raw.get("autofix")
    if not isinstance(autofix_block, dict):
        return LadderSettings()
    ladder_block = autofix_block.get("ladder")
    enabled = False
    if isinstance(ladder_block, dict):
        enabled = bool(ladder_block.get("enabled", False))
    cap_raw = autofix_block.get("cost_cap_per_pr", DEFAULT_COST_CAP_PER_PR_USD)
    try:
        cap = float(cap_raw)
    except (TypeError, ValueError):
        cap = DEFAULT_COST_CAP_PER_PR_USD
    if cap < 0:
        cap = 0.0
    return LadderSettings(enabled=enabled, cost_cap_per_pr_usd=cap)


# ---------------------------------------------------------------------------
# Failure / outcome types
# ---------------------------------------------------------------------------

#: One of the four rungs, identified by stable id string.  Persisted in
#: the audit trail and used as a key in metric labels.
RungId = Literal["rung-0-lint", "rung-1-single-file", "rung-2-multi-file", "rung-3-out-of-scope"]

#: Terminal outcome strings emitted by a rung actor.
LadderOutcome = Literal[
    "applied",  # actor produced a patch and merged it (rung 0).
    "spawned",  # actor handed off to a downstream agent (rung 1/2).
    "stubbed",  # detector matched but actor wiring is deferred.
    "commented",  # actor posted a PR comment without code change (rung 3).
    "skipped",  # detector returned False; rung did not fire.
    "cost_capped",  # rung was selected but exceeded the operator cap.
    "errored",  # actor raised; the daemon caught the exception.
]


@dataclass(frozen=True)
class CIFailure:
    """Normalised CI failure handed to every rung detector.

    The shape is intentionally small so it is cheap for tests to
    fabricate. Anything richer (full log lines, GitHub Checks payload,
    blame attribution) lives upstream in
    :mod:`bernstein.core.autofix.gh_logs` / ``classifier``.

    Attributes:
        repo: ``owner/name`` slug.
        pr_number: Pull-request number.
        head_sha: Head SHA of the PR at attempt time.
        run_id: GitHub Actions run identifier being repaired.
        failing_files: Files reported as failing by the CI adapter.
            Empty when the failure has no file granularity (e.g. the
            installer failed before tests ran).
        pr_touched_files: Files the PR diff modifies. Used by rung 3 to
            detect "out of scope" failures.
        log_excerpt: Truncated failing-log payload. Used by rung 0 to
            sniff for the ``ruff`` / format-drift signal.
        diff_line_count: Estimated patch line count. Used by rung 1 to
            keep its scope small (single file, under 30 lines).
        signature: Stable identifier the operator can group on
            (e.g. the failed pytest node id, or the first stack frame).
    """

    repo: str
    pr_number: int
    head_sha: str
    run_id: str = ""
    failing_files: tuple[str, ...] = field(default_factory=tuple)
    pr_touched_files: tuple[str, ...] = field(default_factory=tuple)
    log_excerpt: str = ""
    diff_line_count: int = 0
    signature: str = ""


@dataclass(frozen=True)
class AutofixOutcome:
    """The terminal outcome of a single ladder rung firing.

    Attributes:
        outcome: One of :data:`LadderOutcome`.
        rung_id: Which rung produced this outcome.
        message: Human-readable summary recorded in the audit trail.
        cost_usd: USD spend attributable to this rung; zero for the
            no-spend rungs (0 and 3) and for stubbed actors.
        commit_sha: Commit SHA when ``outcome == "applied"``;
            empty string otherwise.
    """

    outcome: LadderOutcome
    rung_id: RungId
    message: str = ""
    cost_usd: float = 0.0
    commit_sha: str = ""


# ---------------------------------------------------------------------------
# Detector / actor protocols
# ---------------------------------------------------------------------------


class Detector(Protocol):
    """A rung's matcher. Returns ``True`` when the rung applies."""

    def __call__(self, failure: CIFailure) -> bool: ...


class Actor(Protocol):
    """A rung's actor. Returns the terminal :class:`AutofixOutcome`."""

    def __call__(self, failure: CIFailure) -> AutofixOutcome: ...


# ---------------------------------------------------------------------------
# Rung dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rung:
    """One rung in the autofix ladder.

    Attributes:
        rung_id: Stable identifier persisted to the audit trail.
        description: One-line human-readable description.
        detector: Pure-function matcher; never side-effects.
        actor: Side-effecting action; only invoked after the detector
            matches and the cost cap accepts the rung.
        cost_cap_usd: Maximum spend this rung may incur. The daemon
            refuses to fire a rung whose ``cost_cap_usd`` is greater
            than the operator-configured ``autofix.cost_cap_per_pr``.
    """

    rung_id: RungId
    description: str
    detector: Detector
    actor: Actor
    cost_cap_usd: float


# ---------------------------------------------------------------------------
# Detectors (pure functions)
# ---------------------------------------------------------------------------


# Match a few unambiguous "lint / format drift" signals. These mirror
# the ``config`` patterns in :mod:`bernstein.core.autofix.classifier`
# but the ladder uses a tighter list so rung 0 only fires on payloads
# ``ruff check --fix --diff`` can actually repair.
_LINT_DRIFT_NEEDLES: Final[tuple[str, ...]] = (
    "ruff",
    "would reformat",
    "would-be-reformatted",
    "trailing whitespace",
    "missing-final-newline",
    "isort",
    "import order",
    "would re-format",
)


def detect_lint_drift(failure: CIFailure) -> bool:
    """Match Rung 0 - lint/format drift surfaced by the CI log.

    The matcher looks for the small set of needles ``ruff`` / format
    tools emit. Empty logs and "interesting" logs (security alerts,
    raised exceptions) fall through to a higher rung.
    """
    body = failure.log_excerpt.lower()
    if not body:
        return False
    return any(needle in body for needle in _LINT_DRIFT_NEEDLES)


def detect_single_file_small_diff(failure: CIFailure) -> bool:
    """Match Rung 1 - single failing file with a small diff.

    The rung is conservative: at most one failing file, at most 30
    diff lines, and the file must be one the PR already touches (so we
    do not silently rewrite unrelated code).
    """
    if len(failure.failing_files) != 1:
        return False
    if failure.diff_line_count <= 0 or failure.diff_line_count > 30:
        return False
    failing = failure.failing_files[0]
    return not (failure.pr_touched_files and failing not in failure.pr_touched_files)


def detect_multi_file_pr_touched(failure: CIFailure) -> bool:
    """Match Rung 2 - multi-file failure inside the PR's blast radius.

    Triggers when the failure spans more than one file and at least one
    of the failing files appears in the PR's touched set. Useful for
    follow-up refactors where a rename leaks to N call sites the PR
    forgot.
    """
    if len(failure.failing_files) < 2:
        return False
    if not failure.pr_touched_files:
        # We deliberately refuse to claim multi-file failures with no
        # touched-file context - that case belongs to rung 3.
        return False
    touched = set(failure.pr_touched_files)
    return any(path in touched for path in failure.failing_files)


def detect_out_of_scope(failure: CIFailure) -> bool:
    """Match Rung 3 - failure on file(s) the PR did NOT touch.

    Triggers when every failing file is outside the PR's touched set.
    No failing files plus no touched files means we have nothing to
    correlate - the rung does not claim that case (the daemon will
    fall through to ``no-match``).
    """
    if not failure.failing_files:
        return False
    if not failure.pr_touched_files:
        return False
    touched = set(failure.pr_touched_files)
    return all(path not in touched for path in failure.failing_files)


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------


class LintDriftActor:
    """Rung 0 actor: apply ``ruff check --fix --diff`` and push.

    The actor is intentionally a thin wrapper so tests can inject a
    fake ``apply_patch`` callable. Production wiring lives in the
    autofix daemon, which passes a callable that drives the runner.
    """

    def __init__(self, apply_patch: Callable[[CIFailure], tuple[bool, str, str]]) -> None:
        """Build an actor.

        Args:
            apply_patch: Callable that runs ``ruff check --fix``,
                commits + pushes the patch, and returns
                ``(success, commit_sha, message)``. Tests inject a
                fake; production injects the daemon's runner.
        """
        self._apply_patch = apply_patch

    def __call__(self, failure: CIFailure) -> AutofixOutcome:
        try:
            success, commit_sha, message = self._apply_patch(failure)
        except Exception as exc:  # pragma: no cover - guarded by daemon
            logger.exception("ladder: rung 0 actor raised on %s#%s", failure.repo, failure.pr_number)
            return AutofixOutcome(
                outcome="errored",
                rung_id="rung-0-lint",
                message=f"lint drift actor raised: {exc}",
            )
        if success:
            return AutofixOutcome(
                outcome="applied",
                rung_id="rung-0-lint",
                message=message or "ruff --fix patch applied and pushed",
                cost_usd=0.0,
                commit_sha=commit_sha,
            )
        return AutofixOutcome(
            outcome="skipped",
            rung_id="rung-0-lint",
            message=message or "ruff --fix produced no patch",
        )


def stub_actor(rung_id: RungId, label: str) -> Actor:
    """Return an actor that records detector match but defers action.

    Used for Rung 1 and Rung 2 in this MVP. The actor never spawns a
    downstream agent - it returns ``stubbed`` so the audit log captures
    the would-be escalation and the operator sees the rung is wired
    detector-only. A follow-up PR replaces this with the real spawn.
    """

    def _actor(failure: CIFailure) -> AutofixOutcome:
        return AutofixOutcome(
            outcome="stubbed",
            rung_id=rung_id,
            message=(
                f"{label} detector matched on {failure.repo}#{failure.pr_number}; "
                "actor wiring deferred to a follow-up PR."
            ),
        )

    return _actor


class OutOfScopeActor:
    """Rung 3 actor: post a comment, do nothing else.

    The actor delegates comment posting to a callable so tests can run
    without GitHub. Failure modes raised by the comment poster are
    converted to ``errored`` so the daemon's main loop keeps ticking.
    """

    def __init__(self, post_comment: Callable[[str, int, str], None]) -> None:
        """Build an actor.

        Args:
            post_comment: Callable invoked as
                ``post_comment(repo, pr_number, body)``. Tests inject a
                recorder; production injects the GitHub action adapter.
        """
        self._post_comment = post_comment

    def __call__(self, failure: CIFailure) -> AutofixOutcome:
        body = self._build_body(failure)
        try:
            self._post_comment(failure.repo, failure.pr_number, body)
        except Exception as exc:  # pragma: no cover - guarded by daemon
            logger.exception(
                "ladder: rung 3 comment failed on %s#%s",
                failure.repo,
                failure.pr_number,
            )
            return AutofixOutcome(
                outcome="errored",
                rung_id="rung-3-out-of-scope",
                message=f"out-of-scope comment failed: {exc}",
            )
        return AutofixOutcome(
            outcome="commented",
            rung_id="rung-3-out-of-scope",
            message="out-of-scope comment posted",
        )

    @staticmethod
    def _build_body(failure: CIFailure) -> str:
        listed = ", ".join(f"`{p}`" for p in failure.failing_files[:5])
        if len(failure.failing_files) > 5:
            listed += ", ..."
        touched = ", ".join(f"`{p}`" for p in failure.pr_touched_files[:5])
        if len(failure.pr_touched_files) > 5:
            touched += ", ..."
        return (
            "Autofix ladder: out of scope - human.\n\n"
            f"The CI failure landed on file(s) the PR does not touch: {listed}.\n"
            f"PR-touched file(s): {touched or '(none)'}\n\n"
            "Rung 3 of the autofix ladder declines to act on failures outside the "
            "PR's blast radius. A human reviewer should investigate."
        )


# ---------------------------------------------------------------------------
# Cost caps (per rung) - matches the ticket's MVP table
# ---------------------------------------------------------------------------

COST_CAP_RUNG_0_USD: Final[float] = 0.0
COST_CAP_RUNG_1_USD: Final[float] = 0.20
COST_CAP_RUNG_2_USD: Final[float] = 0.80
COST_CAP_RUNG_3_USD: Final[float] = 0.0


# ---------------------------------------------------------------------------
# Ladder constructor
# ---------------------------------------------------------------------------


def build_default_ladder(
    *,
    apply_lint_patch: Callable[[CIFailure], tuple[bool, str, str]],
    post_comment: Callable[[str, int, str], None],
) -> tuple[Rung, ...]:
    """Build the four-rung ladder with default detectors / actors.

    The ladder is returned in lowest-to-highest order so the daemon's
    "pick the first matching rung" loop also picks the *cheapest*.

    Args:
        apply_lint_patch: Callable for the Rung 0 actor (see
            :class:`LintDriftActor`).
        post_comment: Callable for the Rung 3 actor (see
            :class:`OutOfScopeActor`).

    Returns:
        Tuple of four :class:`Rung` instances in matching order.
    """
    return (
        Rung(
            rung_id="rung-0-lint",
            description="lint / format drift detected by ruff --fix --diff",
            detector=detect_lint_drift,
            actor=LintDriftActor(apply_lint_patch),
            cost_cap_usd=COST_CAP_RUNG_0_USD,
        ),
        Rung(
            rung_id="rung-1-single-file",
            description="single-file test failure with small diff",
            detector=detect_single_file_small_diff,
            actor=stub_actor("rung-1-single-file", "single-file"),
            cost_cap_usd=COST_CAP_RUNG_1_USD,
        ),
        Rung(
            rung_id="rung-2-multi-file",
            description="multi-file failure on PR-touched files",
            detector=detect_multi_file_pr_touched,
            actor=stub_actor("rung-2-multi-file", "multi-file"),
            cost_cap_usd=COST_CAP_RUNG_2_USD,
        ),
        Rung(
            rung_id="rung-3-out-of-scope",
            description="failure on file(s) the PR did NOT touch",
            detector=detect_out_of_scope,
            actor=OutOfScopeActor(post_comment),
            cost_cap_usd=COST_CAP_RUNG_3_USD,
        ),
    )


# ---------------------------------------------------------------------------
# Selection / firing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RungSelection:
    """Result of a dry-run / pre-fire selection.

    Attributes:
        rung: The chosen rung (or ``None`` when no rung matched).
        accepted: ``True`` when the rung is below the operator cost
            cap; ``False`` when the rung is matched but the cap
            refuses it.
        reason: Human-readable explanation suitable for the CLI.
    """

    rung: Rung | None
    accepted: bool
    reason: str


def select_rung(
    ladder: tuple[Rung, ...],
    failure: CIFailure,
    *,
    cost_cap_per_pr: float,
) -> RungSelection:
    """Pick the lowest matching rung subject to the operator cost cap.

    The function is side-effect free so it can be reused by both the
    daemon's dispatch path and the ``--dry-run`` CLI.

    Args:
        ladder: Tuple of rungs in lowest-to-highest order.
        failure: The normalised CI failure.
        cost_cap_per_pr: Operator-configured cap from ``bernstein.yaml``.
            Zero means "unlimited" (matches ``RepoConfig`` semantics).

    Returns:
        A :class:`RungSelection` describing the outcome. ``rung`` is
        ``None`` when no detector matched.
    """
    for rung in ladder:
        try:
            matched = bool(rung.detector(failure))
        except Exception:
            logger.exception("ladder: detector raised for %s", rung.rung_id)
            continue
        if not matched:
            continue
        if cost_cap_per_pr > 0 and rung.cost_cap_usd > cost_cap_per_pr:
            return RungSelection(
                rung=rung,
                accepted=False,
                reason=(
                    f"{rung.rung_id} requires ${rung.cost_cap_usd:.2f} but "
                    f"operator cap is ${cost_cap_per_pr:.2f}; refusing to escalate."
                ),
            )
        return RungSelection(
            rung=rung,
            accepted=True,
            reason=f"{rung.rung_id} matched: {rung.description}",
        )
    return RungSelection(
        rung=None,
        accepted=False,
        reason="no rung matched the failure",
    )


def fire_rung(
    ladder: tuple[Rung, ...],
    failure: CIFailure,
    *,
    cost_cap_per_pr: float,
) -> AutofixOutcome:
    """Select the lowest matching rung and invoke its actor.

    Args:
        ladder: Tuple of rungs in lowest-to-highest order.
        failure: The normalised CI failure.
        cost_cap_per_pr: Operator-configured cap.

    Returns:
        The :class:`AutofixOutcome` produced by the actor. If no rung
        matched, the outcome carries ``outcome="skipped"`` and a
        synthetic ``rung_id`` of ``rung-3-out-of-scope`` so audit
        consumers see a stable value. If the matched rung is refused
        by the cost cap, the outcome carries ``outcome="cost_capped"``
        and the rung's id.
    """
    selection = select_rung(ladder, failure, cost_cap_per_pr=cost_cap_per_pr)
    if selection.rung is None:
        return AutofixOutcome(
            outcome="skipped",
            rung_id="rung-3-out-of-scope",
            message=selection.reason,
        )
    if not selection.accepted:
        return AutofixOutcome(
            outcome="cost_capped",
            rung_id=selection.rung.rung_id,
            message=selection.reason,
            cost_usd=selection.rung.cost_cap_usd,
        )
    return selection.rung.actor(failure)


# ---------------------------------------------------------------------------
# Audit-trail emission
# ---------------------------------------------------------------------------


class AuditEmitter(Protocol):
    """Minimal subset of :class:`AuditLog` the ladder needs.

    Declaring a protocol (instead of importing the AuditLog directly)
    keeps this module test-friendly and avoids a circular import on
    the autofix package init.
    """

    def log(
        self,
        event_type: str,
        actor: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, object] | None = None,
    ) -> object: ...


def emit_ladder_event(
    audit: AuditEmitter,
    *,
    failure: CIFailure,
    outcome: AutofixOutcome,
    event_type: str = "autofix.ladder.fire",
) -> None:
    """Emit the lifecycle event for a ladder fire.

    The event captures everything needed for audit replay: the rung
    id, the failure signature, the outcome, the cost, and the PR /
    run identifiers. ``producer`` is fixed to ``autofix-ladder`` so
    audit consumers can filter on it.
    """
    audit.log(
        event_type=event_type,
        actor="autofix-ladder",
        resource_type="pull_request",
        resource_id=f"{failure.repo}#{failure.pr_number}",
        details={
            "producer": "autofix-ladder",
            "rung_id": outcome.rung_id,
            "failure_signature": failure.signature,
            "outcome": outcome.outcome,
            "message": outcome.message,
            "cost_usd": round(outcome.cost_usd, 6),
            "commit_sha": outcome.commit_sha,
            "head_sha": failure.head_sha,
            "run_id": failure.run_id,
            "failing_files": list(failure.failing_files),
            "pr_touched_files": list(failure.pr_touched_files),
        },
    )


# ---------------------------------------------------------------------------
# Daemon-side coordinator (feature-flag-gated)
# ---------------------------------------------------------------------------


def run_ladder_for_failure(
    *,
    failure: CIFailure,
    settings: LadderSettings,
    ladder: tuple[Rung, ...],
    audit: AuditEmitter | None = None,
) -> AutofixOutcome:
    """Top-level helper the autofix daemon calls to advance one PR.

    Wraps the feature-flag check, lowest-rung selection, actor
    invocation, and audit-trail emit into one call so the daemon's
    main loop has a single integration point.

    Args:
        failure: The normalised CI failure handed in by the daemon.
        settings: Effective ladder settings (feature flag + cap).
        ladder: Tuple of rungs in lowest-to-highest order.
        audit: Optional :class:`AuditEmitter`. When provided every
            non-disabled fire emits one ``autofix.ladder.fire`` event.

    Returns:
        The :class:`AutofixOutcome` produced by the chosen rung. When
        the feature flag is disabled the function returns an outcome
        with ``outcome="skipped"`` and a ``message`` explaining why.
    """
    if not settings.enabled:
        return AutofixOutcome(
            outcome="skipped",
            rung_id="rung-3-out-of-scope",
            message="autofix.ladder.enabled is false; ladder is operator-flagged off.",
        )
    outcome = fire_rung(ladder, failure, cost_cap_per_pr=settings.cost_cap_per_pr_usd)
    if audit is not None:
        try:
            emit_ladder_event(audit, failure=failure, outcome=outcome)
        except Exception:
            logger.exception(
                "ladder: audit emission failed for %s#%s",
                failure.repo,
                failure.pr_number,
            )
    return outcome


__all__ = [
    "COST_CAP_RUNG_0_USD",
    "COST_CAP_RUNG_1_USD",
    "COST_CAP_RUNG_2_USD",
    "COST_CAP_RUNG_3_USD",
    "DEFAULT_COST_CAP_PER_PR_USD",
    "Actor",
    "AuditEmitter",
    "AutofixOutcome",
    "CIFailure",
    "Detector",
    "LadderOutcome",
    "LadderSettings",
    "LintDriftActor",
    "OutOfScopeActor",
    "Rung",
    "RungId",
    "RungSelection",
    "build_default_ladder",
    "detect_lint_drift",
    "detect_multi_file_pr_touched",
    "detect_out_of_scope",
    "detect_single_file_small_diff",
    "emit_ladder_event",
    "fire_rung",
    "load_ladder_settings",
    "run_ladder_for_failure",
    "select_rung",
    "stub_actor",
]
