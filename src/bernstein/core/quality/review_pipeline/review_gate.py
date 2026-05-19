"""Fresh-context, different-model review gate.

This module promotes "review runs in a fresh context, ideally against a
different model than the implementer" from convention to a typed pipeline
stage.  Implementation -> review handoff requires:

* A new session id (no transcript reuse).
* A model-selection rule (``SameModelOk`` / ``DifferentModelPreferred`` /
  ``DifferentModelRequired``).
* A prompt built only from ``(spec, diff, test_output)`` -- the
  implementer's transcript is never passed as priming.

The stage emits a structured :class:`ReviewVerdict` with three possible
states: ``pass``, ``fail``, ``questions``.  The orchestrator must observe
a ``pass`` verdict before auto-merge.  ``fail`` and ``questions`` both
block merge.

When ``DifferentModelRequired`` is set and no alternative model is
configured, the gate raises :class:`EvalGateConfigError` rather than
silently falling back to the same model.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EvalGateConfigError(RuntimeError):
    """Raised when the review gate cannot satisfy its model-selection rule.

    Used when ``ModelSelection.DifferentModelRequired`` is configured but no
    distinct reviewer model is available.  The orchestrator must surface
    this error -- silent fallback to the same model would defeat the gate.
    """


class FreshContextViolation(RuntimeError):
    """Raised when the gate is asked to reuse implementer session state.

    The review gate refuses to accept the implementer's transcript, prior
    bulletin messages, or shared session ids as priming.  Callers that try
    to pass those fields trip this error at gate construction time.
    """


# ---------------------------------------------------------------------------
# Model-selection rule
# ---------------------------------------------------------------------------


class ModelSelection(StrEnum):
    """How strict the reviewer / implementer model-distinction rule is.

    Attributes:
        SameModelOk: Reviewer may use the same model as the implementer.
            Default when an operator deliberately disables cross-model
            review for cost reasons.
        DifferentModelPreferred: Pick a different reviewer model when one
            is available; fall back silently when not.  Logs a warning on
            fallback so the operator can spot the configuration gap.
        DifferentModelRequired: Pick a different reviewer model or refuse
            to run.  Surfaces :class:`EvalGateConfigError` when no
            alternative is configured.
    """

    SameModelOk = "same_model_ok"
    DifferentModelPreferred = "different_model_preferred"
    DifferentModelRequired = "different_model_required"


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


#: Three-valued verdict surfaced by the review gate.
ReviewState = Literal["pass", "fail", "questions"]


@dataclass(frozen=True)
class ReviewVerdict:
    """Structured verdict from the review gate.

    Attributes:
        state: ``pass`` / ``fail`` / ``questions``.  Only ``pass`` permits
            auto-merge; ``fail`` and ``questions`` both block.
        reviewer_model: Model identifier the reviewer ran with.
        reviewer_session_id: Session id assigned to the reviewer.  Always
            distinct from the implementer's session id (the gate enforces
            this at construction time).
        summary: One- or two-sentence rationale for the state.
        issues: Specific issues that drove a ``fail`` verdict.  Empty for
            ``pass`` and ``questions``.
        questions: Open questions for the implementer when the reviewer is
            uncertain.  Non-empty only when ``state == "questions"``.
        confidence: Self-reported 0.0-1.0 confidence; defaults to 1.0 when
            the reviewer did not return one.
    """

    state: ReviewState
    reviewer_model: str
    reviewer_session_id: str
    summary: str = ""
    issues: list[str] = field(default_factory=list[str])
    questions: list[str] = field(default_factory=list[str])
    confidence: float = 1.0

    def blocks_merge(self) -> bool:
        """True when the verdict must prevent auto-merge.

        ``pass`` is the only state that permits auto-merge.  ``fail`` and
        ``questions`` both block -- questions block until the implementer
        answers or escalates because an uncertain reviewer is not a green
        light.
        """
        return self.state != "pass"


# ---------------------------------------------------------------------------
# Inputs to the gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImplementerContext:
    """Minimal record of the implementer run.

    Used for model-selection logic and for the audit trail.  The gate
    never reads :attr:`transcript` -- it exists only so callers can carry
    it for orchestrator-side bookkeeping without smuggling it into the
    review prompt.

    Attributes:
        model: Model identifier the implementer ran with.
        session_id: Implementer session id.  The gate refuses to reuse it.
        transcript: Implementer transcript.  Stored here only so the
            review gate can assert it is *not* threaded into the review
            prompt.  Never passed to the reviewer.
    """

    model: str
    session_id: str
    transcript: str = ""


@dataclass(frozen=True)
class ReviewInputs:
    """The only three inputs allowed into the review prompt.

    The gate builds the reviewer prompt from these fields alone.  Any
    attempt to inject implementer transcript, bulletin history, or other
    session state must be rejected at the caller; the gate itself does
    not read implementer state.

    Attributes:
        spec: Original task / spec text the implementer worked from.
        diff: Unified diff text under review.
        test_output: Test output from the implementer's run (empty string
            when tests were skipped).
    """

    spec: str
    diff: str
    test_output: str = ""


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


# Pluggable reviewer callable.  Receives the assembled prompt + chosen
# model + reviewer session id; returns the raw reviewer response (which
# the caller-supplied parser then folds into a ReviewVerdict).
ReviewerCall = Callable[..., Awaitable[str]]

# Pluggable verdict parser.  Receives the raw reviewer response, the
# chosen model, and the reviewer session id; returns a ReviewVerdict.
VerdictParser = Callable[[str, str, str], ReviewVerdict]


@dataclass(frozen=True)
class ReviewGate:
    """Typed review-pipeline stage with fresh-context hard-asserts.

    The gate is intentionally minimal: it composes an existing reviewer
    callable with the fresh-context + model-selection contract.  Callers
    keep their existing reviewer implementation; the gate enforces the
    handoff rules.

    Attributes:
        model_selection: How strict the reviewer-model distinction is.
        requires_fresh_session: Always True.  Exposed as an attribute so
            downstream code can introspect the contract; mutating it is
            not supported (the dataclass is frozen).
        reviewer_call: Pluggable async function that runs the reviewer.
        verdict_parser: Pluggable function that parses the reviewer's raw
            response into a :class:`ReviewVerdict`.
        prompt_template: Format string that takes ``spec``, ``diff``,
            ``test_output`` keyword arguments and returns the full prompt.
        same_model_warning_logger: Hook for the
            ``DifferentModelPreferred`` warning; defaults to
            :func:`logging.Logger.warning` on the module logger.
    """

    reviewer_call: ReviewerCall
    verdict_parser: VerdictParser
    model_selection: ModelSelection = ModelSelection.DifferentModelPreferred
    requires_fresh_session: bool = True
    prompt_template: str = "## Spec\n\n{spec}\n\n## Diff\n\n{diff}\n\n## Test output\n\n{test_output}\n"

    def __post_init__(self) -> None:
        # ``requires_fresh_session`` is part of the public contract; the
        # gate has no meaning without it.  Reject construction attempts
        # that try to disable it.
        if not self.requires_fresh_session:
            raise FreshContextViolation(
                "ReviewGate requires fresh-session semantics; requires_fresh_session must be True",
            )

    # -- model selection ---------------------------------------------------

    def select_reviewer_model(
        self,
        implementer_model: str,
        *,
        candidates: list[str],
        explicit_reviewer: str | None = None,
    ) -> str:
        """Pick a reviewer model that satisfies :attr:`model_selection`.

        Args:
            implementer_model: Model the implementer ran with.
            candidates: Pool of available reviewer models, in operator-
                preferred order.  May be empty.
            explicit_reviewer: Operator override; when supplied, used
                verbatim unless ``DifferentModelRequired`` rejects it.

        Returns:
            The chosen reviewer model identifier.

        Raises:
            EvalGateConfigError: When ``DifferentModelRequired`` is set
                and no distinct candidate exists.
        """
        if explicit_reviewer is not None:
            if self.model_selection is ModelSelection.DifferentModelRequired and _normalised(
                explicit_reviewer
            ) == _normalised(implementer_model):
                raise EvalGateConfigError(
                    f"DifferentModelRequired: explicit reviewer "
                    f"{explicit_reviewer!r} matches implementer model "
                    f"{implementer_model!r}",
                )
            return explicit_reviewer

        distinct = [c for c in candidates if _normalised(c) != _normalised(implementer_model)]

        if distinct:
            return distinct[0]

        if self.model_selection is ModelSelection.DifferentModelRequired:
            raise EvalGateConfigError(
                "DifferentModelRequired: no reviewer model distinct from "
                f"implementer model {implementer_model!r} is configured "
                f"(candidates={candidates!r})",
            )
        if self.model_selection is ModelSelection.DifferentModelPreferred:
            logger.warning(
                "review_gate: DifferentModelPreferred but no distinct "
                "reviewer candidate; falling back to implementer model %r",
                implementer_model,
            )

        if candidates:
            return candidates[0]
        return implementer_model

    # -- execution ---------------------------------------------------------

    async def run(
        self,
        implementer: ImplementerContext,
        inputs: ReviewInputs,
        *,
        candidates: list[str] | None = None,
        explicit_reviewer: str | None = None,
    ) -> ReviewVerdict:
        """Run the review gate and return a structured verdict.

        Args:
            implementer: Context for the implementer run.  The transcript
                field is read only for an audit-time assertion that it
                does not leak into the prompt.
            inputs: ``(spec, diff, test_output)`` -- the only inputs the
                reviewer sees.
            candidates: Pool of reviewer models, in operator-preferred
                order.  Defaults to the empty list.
            explicit_reviewer: Operator override; bypasses the candidate
                pool when supplied.

        Returns:
            :class:`ReviewVerdict`.

        Raises:
            EvalGateConfigError: When ``DifferentModelRequired`` cannot be
                satisfied.
            FreshContextViolation: When the caller-supplied reviewer
                inputs leak implementer transcript text.  Defensive only;
                the gate never reads :attr:`ImplementerContext.transcript`
                itself.
        """
        reviewer_model = self.select_reviewer_model(
            implementer.model,
            candidates=list(candidates or []),
            explicit_reviewer=explicit_reviewer,
        )

        reviewer_session_id = _new_session_id()
        if reviewer_session_id == implementer.session_id:
            # Astronomically unlikely with token_urlsafe(16), but the
            # contract is "always distinct" so we retry once.
            reviewer_session_id = _new_session_id()

        # Build the prompt strictly from (spec, diff, test_output).  We
        # also verify -- as a paranoid post-condition -- that no slice of
        # the implementer transcript appears in the prompt.  This catches
        # callers who try to smuggle priming through e.g. the spec field.
        prompt = self.prompt_template.format(
            spec=inputs.spec,
            diff=inputs.diff,
            test_output=inputs.test_output,
        )
        _assert_no_transcript_leak(implementer.transcript, prompt)

        started = time.monotonic()
        raw = await self.reviewer_call(
            prompt=prompt,
            model=reviewer_model,
            session_id=reviewer_session_id,
        )
        verdict = self.verdict_parser(raw, reviewer_model, reviewer_session_id)

        elapsed = time.monotonic() - started
        logger.info(
            "review_gate: reviewer=%s state=%s session=%s (%.2fs)",
            reviewer_model,
            verdict.state,
            reviewer_session_id,
            elapsed,
        )
        return verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_session_id() -> str:
    """Return a fresh, unforgeable reviewer session id."""
    return f"review-{secrets.token_urlsafe(16)}"


def _normalised(model: str) -> str:
    """Normalise a model identifier for equality comparison.

    Strips provider prefixes (``anthropic/``, ``openrouter/``) and lower-
    cases, so ``Anthropic/claude-3.5`` and ``claude-3.5`` are treated as
    the same model when checking the distinct-model rule.
    """
    name = model.strip().lower()
    if "/" in name:
        name = name.split("/", 1)[1]
    return name


def _assert_no_transcript_leak(transcript: str, prompt: str) -> None:
    """Raise if a meaningful slice of *transcript* appears verbatim in *prompt*.

    "Meaningful" = at least 80 characters of contiguous transcript text.
    Short overlap is allowed (the spec and the implementer's transcript
    may share boilerplate); the threshold is intentionally generous so
    legitimate spec quotes do not trip a false alarm.
    """
    if not transcript:
        return
    sample_size = 80
    if len(transcript) < sample_size:
        return
    # Probe a few positions across the transcript -- not exhaustive, but
    # enough to catch a caller who concatenates the whole transcript into
    # the spec field.
    for start in (0, len(transcript) // 3, (len(transcript) * 2) // 3):
        chunk = transcript[start : start + sample_size]
        if chunk and chunk in prompt:
            raise FreshContextViolation(
                "review gate detected implementer transcript content "
                "leaking into reviewer prompt; build the prompt from "
                "(spec, diff, test_output) only",
            )


def parse_structured_verdict(
    raw: str,
    reviewer_model: str,
    reviewer_session_id: str,
) -> ReviewVerdict:
    """Default :class:`ReviewVerdict` parser for JSON reviewer responses.

    Accepts a JSON object with shape::

        {
          "state": "pass" | "fail" | "questions",
          "summary": "...",
          "issues": ["..."],
          "questions": ["..."],
          "confidence": 0.0..1.0
        }

    Unknown ``state`` values map to ``fail`` (safe default that blocks
    merge).  Parse failures also map to ``fail`` so a reviewer outage
    never silently green-lights a merge.
    """
    import json

    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ReviewVerdict(
            state="fail",
            reviewer_model=reviewer_model,
            reviewer_session_id=reviewer_session_id,
            summary=f"reviewer returned unparseable response: {text[:200]}",
            issues=["unparseable reviewer response"],
        )

    if not isinstance(data, dict):
        return ReviewVerdict(
            state="fail",
            reviewer_model=reviewer_model,
            reviewer_session_id=reviewer_session_id,
            summary=f"reviewer response was not an object: {type(data).__name__}",
            issues=["non-object reviewer response"],
        )

    raw_state = str(data.get("state", "fail")).lower()
    state: ReviewState
    if raw_state == "pass":
        state = "pass"
    elif raw_state == "questions":
        state = "questions"
    else:
        state = "fail"

    issues_raw = data.get("issues", [])
    issues: list[str] = [str(i) for i in issues_raw] if isinstance(issues_raw, list) else []
    questions_raw = data.get("questions", [])
    questions: list[str] = [str(q) for q in questions_raw] if isinstance(questions_raw, list) else []

    confidence_raw = data.get("confidence", 1.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = max(0.0, min(1.0, confidence))

    return ReviewVerdict(
        state=state,
        reviewer_model=reviewer_model,
        reviewer_session_id=reviewer_session_id,
        summary=str(data.get("summary", "")),
        issues=issues,
        questions=questions,
        confidence=confidence,
    )
