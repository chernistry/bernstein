"""Unit tests for the fresh-context review gate.

Covers the four contract properties called out in the spec:

* Fresh-context separation -- reviewer session id differs from the
  implementer session id and the implementer transcript never reaches the
  reviewer prompt.
* Structured-verdict schema -- the parser maps reviewer JSON onto a
  three-valued ``ReviewVerdict`` and clamps malformed inputs to ``fail``.
* Fail-blocks-merge path -- ``ReviewVerdict.blocks_merge()`` returns True
  for ``fail``.
* Questions-block-merge path -- ``ReviewVerdict.blocks_merge()`` returns
  True for ``questions``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bernstein.core.quality.review_pipeline import (
    EvalGateConfigError,
    FreshContextViolation,
    ImplementerContext,
    ModelSelection,
    ReviewGate,
    ReviewInputs,
    ReviewVerdict,
    parse_structured_verdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _impl(model: str = "anthropic/claude-impl", *, transcript: str = "") -> ImplementerContext:
    return ImplementerContext(model=model, session_id="impl-session-xyz", transcript=transcript)


def _inputs(*, spec: str = "do X", diff: str = "+ added", tests: str = "ok") -> ReviewInputs:
    return ReviewInputs(spec=spec, diff=diff, test_output=tests)


def _make_gate(
    raw_response: str,
    *,
    selection: ModelSelection = ModelSelection.DifferentModelPreferred,
) -> tuple[ReviewGate, dict[str, object]]:
    """Build a ReviewGate whose reviewer returns *raw_response*.

    Returns the gate plus a dict capturing the prompt + kwargs seen by
    the reviewer call so tests can assert on what the gate actually sent.
    """
    captured: dict[str, object] = {}

    async def reviewer(*, prompt: str, model: str, session_id: str) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        captured["session_id"] = session_id
        return raw_response

    gate = ReviewGate(
        reviewer_call=reviewer,
        verdict_parser=parse_structured_verdict,
        model_selection=selection,
    )
    return gate, captured


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fresh-context separation
# ---------------------------------------------------------------------------


class TestFreshContextSeparation:
    def test_reviewer_session_distinct_from_implementer(self) -> None:
        gate, captured = _make_gate(json.dumps({"state": "pass", "summary": "lgtm", "issues": []}))
        verdict = _run(
            gate.run(
                _impl(),
                _inputs(),
                candidates=["openai/gpt-review"],
            )
        )
        assert isinstance(verdict, ReviewVerdict)
        assert verdict.reviewer_session_id != "impl-session-xyz"
        assert captured["session_id"] == verdict.reviewer_session_id

    def test_implementer_transcript_never_reaches_prompt(self) -> None:
        # A long, distinctive transcript -- if any 80-char slice leaks
        # into the prompt, the gate raises.
        secret_transcript = "X" * 200 + "implementer-private-thoughts-" * 5
        gate, captured = _make_gate(json.dumps({"state": "pass", "summary": "ok", "issues": []}))
        verdict = _run(
            gate.run(
                _impl(transcript=secret_transcript),
                _inputs(spec="public spec text"),
                candidates=["openai/gpt-review"],
            )
        )
        assert verdict.state == "pass"
        prompt = str(captured["prompt"])
        # The reviewer never sees the transcript.
        assert "implementer-private-thoughts" not in prompt

    def test_prompt_built_only_from_spec_diff_test_output(self) -> None:
        gate, captured = _make_gate(json.dumps({"state": "pass", "summary": "ok", "issues": []}))
        _run(
            gate.run(
                _impl(transcript="hidden transcript content"),
                _inputs(spec="SPEC-MARK", diff="DIFF-MARK", tests="TESTS-MARK"),
                candidates=["openai/gpt-review"],
            )
        )
        prompt = str(captured["prompt"])
        assert "SPEC-MARK" in prompt
        assert "DIFF-MARK" in prompt
        assert "TESTS-MARK" in prompt
        assert "hidden transcript content" not in prompt

    def test_transcript_smuggled_through_spec_is_rejected(self) -> None:
        # If a caller tries to paste the implementer transcript into the
        # spec field, the post-condition check should catch it.
        long_transcript = "leaked-priming-fragment-" * 20
        gate, _captured = _make_gate(json.dumps({"state": "pass", "summary": "ok", "issues": []}))
        with pytest.raises(FreshContextViolation):
            _run(
                gate.run(
                    _impl(transcript=long_transcript),
                    _inputs(spec=f"do X\n\n{long_transcript}"),
                    candidates=["openai/gpt-review"],
                )
            )

    def test_gate_refuses_fresh_session_disabled(self) -> None:
        # The dataclass post-init asserts requires_fresh_session is True.
        with pytest.raises(FreshContextViolation):
            ReviewGate(
                reviewer_call=_make_gate("{}")[0].reviewer_call,
                verdict_parser=parse_structured_verdict,
                requires_fresh_session=False,
            )


# ---------------------------------------------------------------------------
# Model-selection rule
# ---------------------------------------------------------------------------


class TestModelSelection:
    def test_different_model_required_picks_distinct(self) -> None:
        gate, captured = _make_gate(
            json.dumps({"state": "pass", "summary": "ok"}),
            selection=ModelSelection.DifferentModelRequired,
        )
        _run(
            gate.run(
                _impl(model="anthropic/claude-impl"),
                _inputs(),
                candidates=["anthropic/claude-impl", "openai/gpt-review"],
            )
        )
        # The implementer-matching candidate is filtered out.
        assert captured["model"] == "openai/gpt-review"

    def test_different_model_required_raises_when_no_alternative(self) -> None:
        gate, _captured = _make_gate(
            "{}",
            selection=ModelSelection.DifferentModelRequired,
        )
        with pytest.raises(EvalGateConfigError):
            _run(
                gate.run(
                    _impl(model="anthropic/claude-impl"),
                    _inputs(),
                    candidates=["anthropic/claude-impl"],
                )
            )

    def test_different_model_required_raises_on_empty_candidates(self) -> None:
        gate, _captured = _make_gate(
            "{}",
            selection=ModelSelection.DifferentModelRequired,
        )
        with pytest.raises(EvalGateConfigError):
            _run(
                gate.run(
                    _impl(model="anthropic/claude-impl"),
                    _inputs(),
                    candidates=[],
                )
            )

    def test_different_model_preferred_falls_back(self, caplog: pytest.LogCaptureFixture) -> None:
        gate, captured = _make_gate(
            json.dumps({"state": "pass", "summary": "ok"}),
            selection=ModelSelection.DifferentModelPreferred,
        )
        with caplog.at_level("WARNING"):
            _run(
                gate.run(
                    _impl(model="anthropic/claude-impl"),
                    _inputs(),
                    candidates=["anthropic/claude-impl"],
                )
            )
        # Fell back to the implementer model with a warning.
        assert captured["model"] == "anthropic/claude-impl"
        assert any("DifferentModelPreferred" in rec.message for rec in caplog.records)

    def test_same_model_ok_accepts_any(self) -> None:
        gate, captured = _make_gate(
            json.dumps({"state": "pass", "summary": "ok"}),
            selection=ModelSelection.SameModelOk,
        )
        _run(
            gate.run(
                _impl(model="anthropic/claude-impl"),
                _inputs(),
                candidates=["anthropic/claude-impl"],
            )
        )
        assert captured["model"] == "anthropic/claude-impl"

    def test_explicit_override_used_when_distinct(self) -> None:
        gate, captured = _make_gate(
            json.dumps({"state": "pass", "summary": "ok"}),
            selection=ModelSelection.DifferentModelRequired,
        )
        _run(
            gate.run(
                _impl(model="anthropic/claude-impl"),
                _inputs(),
                candidates=[],
                explicit_reviewer="openai/gpt-review",
            )
        )
        assert captured["model"] == "openai/gpt-review"

    def test_explicit_override_rejected_when_matches_implementer(self) -> None:
        gate, _captured = _make_gate(
            "{}",
            selection=ModelSelection.DifferentModelRequired,
        )
        with pytest.raises(EvalGateConfigError):
            _run(
                gate.run(
                    _impl(model="anthropic/claude-impl"),
                    _inputs(),
                    candidates=["openai/gpt-review"],
                    explicit_reviewer="anthropic/claude-impl",
                )
            )

    def test_model_match_ignores_provider_prefix(self) -> None:
        # "anthropic/claude-foo" and "claude-foo" should count as the
        # same model when checking the distinct-model rule.
        gate, _captured = _make_gate(
            "{}",
            selection=ModelSelection.DifferentModelRequired,
        )
        with pytest.raises(EvalGateConfigError):
            _run(
                gate.run(
                    _impl(model="anthropic/claude-foo"),
                    _inputs(),
                    candidates=["claude-foo"],
                )
            )


# ---------------------------------------------------------------------------
# Structured-verdict schema
# ---------------------------------------------------------------------------


class TestStructuredVerdictSchema:
    def test_pass_round_trip(self) -> None:
        raw = json.dumps(
            {
                "state": "pass",
                "summary": "good",
                "issues": [],
                "confidence": 0.9,
            }
        )
        verdict = parse_structured_verdict(raw, "openai/gpt-review", "sid")
        assert verdict.state == "pass"
        assert verdict.summary == "good"
        assert verdict.issues == []
        assert verdict.confidence == pytest.approx(0.9)
        assert verdict.reviewer_model == "openai/gpt-review"
        assert verdict.reviewer_session_id == "sid"

    def test_fail_with_issues(self) -> None:
        raw = json.dumps(
            {
                "state": "fail",
                "summary": "broken",
                "issues": ["off-by-one in loop", "missing test for edge"],
            }
        )
        verdict = parse_structured_verdict(raw, "m", "s")
        assert verdict.state == "fail"
        assert len(verdict.issues) == 2
        assert verdict.blocks_merge() is True

    def test_questions_state(self) -> None:
        raw = json.dumps(
            {
                "state": "questions",
                "summary": "uncertain",
                "questions": ["Why was X removed?"],
            }
        )
        verdict = parse_structured_verdict(raw, "m", "s")
        assert verdict.state == "questions"
        assert verdict.questions == ["Why was X removed?"]

    def test_unknown_state_maps_to_fail(self) -> None:
        raw = json.dumps({"state": "maybe?", "summary": "noisy"})
        verdict = parse_structured_verdict(raw, "m", "s")
        assert verdict.state == "fail"
        assert verdict.blocks_merge() is True

    def test_unparseable_response_maps_to_fail(self) -> None:
        verdict = parse_structured_verdict("not json at all", "m", "s")
        assert verdict.state == "fail"
        assert verdict.blocks_merge() is True

    def test_non_object_response_maps_to_fail(self) -> None:
        verdict = parse_structured_verdict(json.dumps([1, 2]), "m", "s")
        assert verdict.state == "fail"
        assert verdict.issues  # carries an explanation

    def test_confidence_clamped_to_unit_interval(self) -> None:
        raw = json.dumps({"state": "pass", "confidence": 2.5})
        verdict = parse_structured_verdict(raw, "m", "s")
        assert 0.0 <= verdict.confidence <= 1.0

    def test_code_fenced_response_parsed(self) -> None:
        raw = "```json\n" + json.dumps({"state": "pass"}) + "\n```"
        verdict = parse_structured_verdict(raw, "m", "s")
        assert verdict.state == "pass"


# ---------------------------------------------------------------------------
# Fail-blocks-merge
# ---------------------------------------------------------------------------


class TestFailBlocksMerge:
    def test_fail_verdict_blocks_merge(self) -> None:
        gate, _captured = _make_gate(
            json.dumps(
                {
                    "state": "fail",
                    "summary": "regression",
                    "issues": ["test_x fails after diff"],
                }
            )
        )
        verdict = _run(gate.run(_impl(), _inputs(), candidates=["openai/gpt-review"]))
        assert isinstance(verdict, ReviewVerdict)
        assert verdict.state == "fail"
        assert verdict.blocks_merge() is True
        assert verdict.issues == ["test_x fails after diff"]


# ---------------------------------------------------------------------------
# Questions-blocks-merge
# ---------------------------------------------------------------------------


class TestQuestionsBlocksMerge:
    def test_questions_verdict_blocks_merge(self) -> None:
        gate, _captured = _make_gate(
            json.dumps(
                {
                    "state": "questions",
                    "summary": "need clarification",
                    "questions": ["Is field Y still needed?"],
                }
            )
        )
        verdict = _run(gate.run(_impl(), _inputs(), candidates=["openai/gpt-review"]))
        assert isinstance(verdict, ReviewVerdict)
        assert verdict.state == "questions"
        assert verdict.blocks_merge() is True
        assert verdict.questions == ["Is field Y still needed?"]
        # Questions verdict has no issues -- it is uncertainty, not failure.
        assert verdict.issues == []

    def test_pass_verdict_does_not_block_merge(self) -> None:
        gate, _captured = _make_gate(json.dumps({"state": "pass", "summary": "lgtm"}))
        verdict = _run(gate.run(_impl(), _inputs(), candidates=["openai/gpt-review"]))
        assert isinstance(verdict, ReviewVerdict)
        assert verdict.state == "pass"
        assert verdict.blocks_merge() is False
