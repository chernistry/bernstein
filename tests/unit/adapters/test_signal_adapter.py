"""Tests for the adapter ``stream_signal_parser`` hook.

These cover the default delegation to the canonical parser, the
override path used by adapters that map a native protocol onto the
canonical vocabulary, the conformance-side terminal-signal check, and
graceful handling of unknown / malformed signals on a live stream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.base import CLIAdapter, SpawnResult
from bernstein.adapters.conformance import (
    ConformanceReport,
    check_terminal_signal,
)
from bernstein.core.protocols.stream_signals import (
    MissingTerminalSignal,
    SignalKind,
    StreamSignal,
    format_signal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubAdapter(CLIAdapter):
    """Minimal adapter used to exercise the default parser hook."""

    def name(self) -> str:
        return "stub"

    def spawn(  # type: ignore[override]
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> SpawnResult:
        # The test never invokes spawn(); we only need the parser hook.
        raise NotImplementedError("not used in these tests")


class _NativeJsonAdapter(CLIAdapter):
    """Adapter that emits a fake native protocol and translates it.

    The fictional native format is one JSON object per line with a
    ``type`` field. The adapter overrides ``stream_signal_parser`` to
    map ``{"type":"finish","ok":true}`` onto :attr:`SignalKind.COMPLETED`,
    ``{"type":"finish","ok":false}`` onto :attr:`SignalKind.FAILED`,
    and ``{"type":"ask","prompt":..., "choices":[...]}`` onto
    :attr:`SignalKind.QUESTION`.
    """

    def name(self) -> str:
        return "nativejson"

    def spawn(  # type: ignore[override]
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> SpawnResult:
        raise NotImplementedError("not used in these tests")

    def stream_signal_parser(self, line: str) -> StreamSignal | None:
        import json as _json

        stripped = line.strip()
        if not stripped.startswith("{"):
            # Fall back to canonical grammar so this adapter still
            # accepts pass-through BERNSTEIN: lines emitted from
            # downstream tools.
            return super().stream_signal_parser(line)  # type: ignore[return-value]
        try:
            obj = _json.loads(stripped)
        except _json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        kind = obj.get("type")
        if kind == "finish":
            return StreamSignal(
                kind=SignalKind.COMPLETED if obj.get("ok") else SignalKind.FAILED,
                payload={k: v for k, v in obj.items() if k not in {"type"}},
                raw_line=stripped,
            )
        if kind == "ask":
            return StreamSignal(
                kind=SignalKind.QUESTION,
                payload={
                    "question": obj.get("prompt", ""),
                    "options": obj.get("choices", []),
                },
                raw_line=stripped,
            )
        return None


# ---------------------------------------------------------------------------
# Default parser hook
# ---------------------------------------------------------------------------


class TestDefaultParser:
    """Default ``stream_signal_parser`` delegates to canonical parsing."""

    def test_default_parses_canonical_completed(self) -> None:
        adapter = _StubAdapter()
        result = adapter.stream_signal_parser("BERNSTEIN:COMPLETED")
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.COMPLETED

    def test_default_parses_canonical_question_with_payload(self) -> None:
        adapter = _StubAdapter()
        line = format_signal(SignalKind.QUESTION, {"question": "go?", "options": ["y", "n"]})
        result = adapter.stream_signal_parser(line)
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.QUESTION
        assert result.payload["question"] == "go?"

    def test_default_returns_none_for_plain_log_line(self) -> None:
        adapter = _StubAdapter()
        assert adapter.stream_signal_parser("just a log line") is None

    def test_default_returns_none_for_unknown_kind(self) -> None:
        adapter = _StubAdapter()
        assert adapter.stream_signal_parser("BERNSTEIN:NOPE_NOT_A_KIND") is None

    def test_default_returns_none_for_malformed_payload(self) -> None:
        adapter = _StubAdapter()
        assert adapter.stream_signal_parser("BERNSTEIN:QUESTION {bad json}") is None


# ---------------------------------------------------------------------------
# Override path: native protocol translation
# ---------------------------------------------------------------------------


class TestOverriddenParser:
    """Adapters that override the hook can map native events to canonical."""

    def test_native_finish_ok_maps_to_completed(self) -> None:
        adapter = _NativeJsonAdapter()
        result = adapter.stream_signal_parser('{"type":"finish","ok":true}')
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.COMPLETED
        assert result.is_terminal

    def test_native_finish_fail_maps_to_failed(self) -> None:
        adapter = _NativeJsonAdapter()
        result = adapter.stream_signal_parser('{"type":"finish","ok":false}')
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.FAILED
        assert result.is_terminal

    def test_native_ask_maps_to_question(self) -> None:
        adapter = _NativeJsonAdapter()
        result = adapter.stream_signal_parser('{"type":"ask","prompt":"go?","choices":["y","n"]}')
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.QUESTION
        assert result.payload["question"] == "go?"
        assert result.payload["options"] == ["y", "n"]

    def test_override_falls_back_to_canonical(self) -> None:
        """Override still accepts plain canonical signals."""
        adapter = _NativeJsonAdapter()
        result = adapter.stream_signal_parser('BERNSTEIN:BLOCKED {"reason":"x"}')
        assert isinstance(result, StreamSignal)
        assert result.kind is SignalKind.BLOCKED

    def test_override_drops_unknown_native_type(self) -> None:
        adapter = _NativeJsonAdapter()
        assert adapter.stream_signal_parser('{"type":"unrelated"}') is None

    def test_override_drops_broken_json(self) -> None:
        adapter = _NativeJsonAdapter()
        assert adapter.stream_signal_parser('{"type":"finish"') is None


# ---------------------------------------------------------------------------
# Question round-trip through approval/elicitation-style flow
# ---------------------------------------------------------------------------


class TestQuestionRoundTrip:
    """A QUESTION signal carries enough payload to round-trip a reply."""

    def test_question_payload_round_trips_through_reply_envelope(self) -> None:
        adapter = _StubAdapter()
        question = adapter.stream_signal_parser(
            format_signal(
                SignalKind.QUESTION,
                {"question": "Proceed?", "options": ["y", "n"], "id": "q-1"},
            )
        )
        assert isinstance(question, StreamSignal)

        # Simulate the orchestrator constructing an inbound reply
        # envelope keyed on the question id and routing it back through
        # the adapter's stdin/IPC layer.
        reply_envelope = {
            "in_reply_to": question.payload["id"],
            "answer": "y",
        }
        assert reply_envelope["in_reply_to"] == "q-1"
        assert reply_envelope["answer"] in question.payload["options"]


# ---------------------------------------------------------------------------
# Plan-handoff signals
# ---------------------------------------------------------------------------


class TestPlanHandoff:
    """``PLAN_DRAFT`` / ``PLAN_READY`` carry the markdown/path payload."""

    def test_plan_draft_carries_markdown(self) -> None:
        adapter = _StubAdapter()
        line = format_signal(SignalKind.PLAN_DRAFT, {"markdown": "# Plan\n- step 1"})
        sig = adapter.stream_signal_parser(line)
        assert isinstance(sig, StreamSignal)
        assert sig.kind is SignalKind.PLAN_DRAFT
        assert sig.payload["markdown"].startswith("# Plan")

    def test_plan_ready_points_at_sdd_artefact(self) -> None:
        adapter = _StubAdapter()
        line = format_signal(SignalKind.PLAN_READY, {"path": ".sdd/plans/foo.md"})
        sig = adapter.stream_signal_parser(line)
        assert isinstance(sig, StreamSignal)
        assert sig.kind is SignalKind.PLAN_READY
        assert sig.payload["path"].startswith(".sdd/")


# ---------------------------------------------------------------------------
# Conformance: terminal-signal check
# ---------------------------------------------------------------------------


class TestConformanceTerminalCheck:
    """``check_terminal_signal`` flags adapter runs that never finish."""

    def test_run_with_completed_passes(self) -> None:
        warning = check_terminal_signal(
            ["working...", "BERNSTEIN:COMPLETED"],
            run_id="run-1",
        )
        assert warning is None

    def test_run_with_failed_passes(self) -> None:
        warning = check_terminal_signal(
            ["BERNSTEIN:FAILED"],
            run_id="run-2",
        )
        assert warning is None

    def test_run_without_terminal_signal_warns(self) -> None:
        warning = check_terminal_signal(
            [
                "working...",
                'BERNSTEIN:QUESTION {"question":"?"}',
                "done writing files",
            ],
            run_id="run-3",
        )
        assert isinstance(warning, MissingTerminalSignal)
        assert "run-3" in str(warning)

    def test_run_with_unknown_signals_still_warns(self) -> None:
        warning = check_terminal_signal(
            ["BERNSTEIN:UNKNOWN_KIND", "noisy log"],
            run_id="run-4",
        )
        assert isinstance(warning, MissingTerminalSignal)


class TestConformanceReport:
    """``ConformanceReport`` exposes the missing-terminal-signal list."""

    def test_report_serialises_missing_terminal_field(self) -> None:
        report = ConformanceReport()
        report.missing_terminal_signal.append("adapter:nojson")
        out = report.to_dict()
        assert "missing_terminal_signal" in out
        assert out["missing_terminal_signal"] == ["adapter:nojson"]


# ---------------------------------------------------------------------------
# Resilience: unknown signal does not poison the stream
# ---------------------------------------------------------------------------


def test_unknown_signal_is_skipped_in_stream() -> None:
    """One bad signal between two good ones must not break the others."""
    adapter = _StubAdapter()
    lines = [
        'BERNSTEIN:PLAN_DRAFT {"markdown":"# a"}',
        "BERNSTEIN:UNSUPPORTED_KIND",
        "BERNSTEIN:QUESTION {malformed",
        "BERNSTEIN:COMPLETED",
    ]
    parsed = [adapter.stream_signal_parser(ln) for ln in lines]
    kinds = [p.kind for p in parsed if isinstance(p, StreamSignal)]
    assert kinds == [SignalKind.PLAN_DRAFT, SignalKind.COMPLETED]


@pytest.mark.parametrize("kind", list(SignalKind))
def test_every_signal_kind_parses_through_adapter(kind: SignalKind) -> None:
    """Every canonical kind must round-trip through an adapter."""
    adapter = _StubAdapter()
    line = format_signal(kind)
    sig = adapter.stream_signal_parser(line)
    assert isinstance(sig, StreamSignal)
    assert sig.kind is kind
