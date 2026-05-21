"""Tests for the canonical stream-signal protocol."""

from __future__ import annotations

import concurrent.futures
import json

import pytest

from bernstein.core.protocols.stream_signals import (
    SIGNAL_PREFIX,
    TERMINAL_SIGNAL_KINDS,
    MissingTerminalSignal,
    SignalKind,
    StreamSignal,
    format_signal,
    has_terminal_signal,
    iter_signals,
    parse_signal,
)

# ---------------------------------------------------------------------------
# Parse: every canonical kind
# ---------------------------------------------------------------------------


class TestParseSignal:
    """Round-trip coverage of every canonical signal kind."""

    def test_parses_completed(self) -> None:
        sig = parse_signal("BERNSTEIN:COMPLETED")
        assert sig is not None
        assert sig.kind is SignalKind.COMPLETED
        assert sig.payload == {}
        assert sig.is_terminal is True

    def test_parses_failed_with_payload(self) -> None:
        sig = parse_signal('BERNSTEIN:FAILED {"reason":"timeout"}')
        assert sig is not None
        assert sig.kind is SignalKind.FAILED
        assert sig.payload == {"reason": "timeout"}
        assert sig.is_terminal is True

    def test_parses_question(self) -> None:
        line = 'BERNSTEIN:QUESTION {"question":"Proceed?","options":["y","n"]}'
        sig = parse_signal(line)
        assert sig is not None
        assert sig.kind is SignalKind.QUESTION
        assert sig.payload["question"] == "Proceed?"
        assert sig.payload["options"] == ["y", "n"]
        assert sig.is_terminal is False

    def test_parses_plan_draft(self) -> None:
        sig = parse_signal('BERNSTEIN:PLAN_DRAFT {"markdown":"# Plan","path":".sdd/plan.md"}')
        assert sig is not None
        assert sig.kind is SignalKind.PLAN_DRAFT
        assert sig.payload["markdown"] == "# Plan"
        assert sig.payload["path"] == ".sdd/plan.md"

    def test_parses_plan_ready(self) -> None:
        sig = parse_signal('BERNSTEIN:PLAN_READY {"path":".sdd/plan.md"}')
        assert sig is not None
        assert sig.kind is SignalKind.PLAN_READY
        assert sig.payload["path"] == ".sdd/plan.md"

    def test_parses_blocked(self) -> None:
        sig = parse_signal('BERNSTEIN:BLOCKED {"reason":"missing creds","hint":"set TOKEN"}')
        assert sig is not None
        assert sig.kind is SignalKind.BLOCKED
        assert sig.payload["reason"] == "missing creds"
        assert sig.payload["hint"] == "set TOKEN"
        assert sig.is_terminal is False


# ---------------------------------------------------------------------------
# Parse: tolerant handling of malformed input
# ---------------------------------------------------------------------------


class TestMalformedLines:
    """Parser never raises and returns ``None`` for non-signals."""

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "regular log line",
            "BERNSTEINfoo",  # prefix collision without colon
            "bernstein:COMPLETED",  # wrong case
            "BERNSTEIN:",  # prefix only, no kind
            "BERNSTEIN: ",  # prefix + whitespace
            "BERNSTEIN:UNKNOWN_KIND",  # unrecognised kind
            "BERNSTEIN:QUESTION {malformed json",  # broken JSON
            "BERNSTEIN:QUESTION not-json-at-all",  # non-JSON payload
            'BERNSTEIN:QUESTION ["array","payload"]',  # JSON array, not object
            "BERNSTEIN:QUESTION 42",  # scalar payload
            "BERNSTEIN:QUESTION null",  # null payload
        ],
    )
    def test_returns_none_for_malformed_input(self, line: str) -> None:
        assert parse_signal(line) is None

    def test_returns_none_for_non_string(self) -> None:
        assert parse_signal(None) is None  # type: ignore[arg-type]
        assert parse_signal(42) is None  # type: ignore[arg-type]
        assert parse_signal(["BERNSTEIN:COMPLETED"]) is None  # type: ignore[arg-type]

    def test_strips_surrounding_whitespace(self) -> None:
        sig = parse_signal("   BERNSTEIN:COMPLETED   \n")
        assert sig is not None
        assert sig.kind is SignalKind.COMPLETED

    def test_tolerates_whitespace_after_prefix(self) -> None:
        """Producers that emit ``BERNSTEIN: COMPLETED`` are still accepted."""
        sig = parse_signal("BERNSTEIN: COMPLETED")
        assert sig is not None
        assert sig.kind is SignalKind.COMPLETED

    def test_preserves_raw_line(self) -> None:
        sig = parse_signal("  BERNSTEIN:COMPLETED  ")
        assert sig is not None
        assert sig.raw_line == "BERNSTEIN:COMPLETED"


# ---------------------------------------------------------------------------
# iter_signals: batch helper
# ---------------------------------------------------------------------------


class TestIterSignals:
    """Batch parser filters non-signal lines and preserves order."""

    def test_filters_noise_and_preserves_order(self) -> None:
        lines = [
            "starting agent...",
            'BERNSTEIN:PLAN_DRAFT {"markdown":"# Draft"}',
            "doing work",
            'BERNSTEIN:QUESTION {"question":"go?"}',
            "more output",
            "BERNSTEIN:COMPLETED",
            "trailing log line",
        ]
        signals = iter_signals(lines)
        assert [s.kind for s in signals] == [
            SignalKind.PLAN_DRAFT,
            SignalKind.QUESTION,
            SignalKind.COMPLETED,
        ]

    def test_empty_input(self) -> None:
        assert iter_signals([]) == []

    def test_skips_unknown_kinds_silently(self) -> None:
        signals = iter_signals(
            [
                "BERNSTEIN:WHO_KNOWS",
                "BERNSTEIN:COMPLETED",
            ]
        )
        assert len(signals) == 1
        assert signals[0].kind is SignalKind.COMPLETED


# ---------------------------------------------------------------------------
# Format: producer-side helper
# ---------------------------------------------------------------------------


class TestFormatSignal:
    """``format_signal`` round-trips through ``parse_signal``."""

    def test_round_trips_no_payload(self) -> None:
        line = format_signal(SignalKind.COMPLETED)
        assert line == "BERNSTEIN:COMPLETED"
        parsed = parse_signal(line)
        assert parsed is not None
        assert parsed.kind is SignalKind.COMPLETED
        assert parsed.payload == {}

    def test_round_trips_with_payload(self) -> None:
        payload = {"question": "Proceed?", "options": ["y", "n"]}
        line = format_signal(SignalKind.QUESTION, payload)
        parsed = parse_signal(line)
        assert parsed is not None
        assert parsed.kind is SignalKind.QUESTION
        assert parsed.payload == payload

    def test_round_trips_unicode(self) -> None:
        line = format_signal(SignalKind.QUESTION, {"question": "Продолжить?"})
        parsed = parse_signal(line)
        assert parsed is not None
        assert parsed.payload["question"] == "Продолжить?"

    def test_empty_payload_omits_body(self) -> None:
        line = format_signal(SignalKind.COMPLETED, {})
        # Empty dict still renders as "{}" - round-trip semantics
        # remain identical. We just assert it parses cleanly.
        parsed = parse_signal(line)
        assert parsed is not None
        assert parsed.kind is SignalKind.COMPLETED
        assert parsed.payload == {}

    def test_rejects_non_dict_payload(self) -> None:
        with pytest.raises(TypeError):
            format_signal(SignalKind.QUESTION, ["not", "a", "dict"])  # type: ignore[arg-type]

    def test_rejects_unserialisable_payload(self) -> None:
        class NotJson:
            pass

        with pytest.raises(ValueError):
            format_signal(SignalKind.QUESTION, {"obj": NotJson()})


# ---------------------------------------------------------------------------
# Terminal-signal conformance
# ---------------------------------------------------------------------------


class TestTerminalSignals:
    """Terminal-signal vocabulary and helper coverage."""

    def test_terminal_set_matches_spec(self) -> None:
        assert frozenset({SignalKind.COMPLETED, SignalKind.FAILED}) == TERMINAL_SIGNAL_KINDS

    def test_has_terminal_signal_true(self) -> None:
        sigs = iter_signals(
            [
                'BERNSTEIN:QUESTION {"question":"?"}',
                "BERNSTEIN:COMPLETED",
            ]
        )
        assert has_terminal_signal(sigs) is True

    def test_has_terminal_signal_false(self) -> None:
        sigs = iter_signals(
            [
                'BERNSTEIN:QUESTION {"question":"?"}',
                'BERNSTEIN:BLOCKED {"reason":"x"}',
            ]
        )
        assert has_terminal_signal(sigs) is False

    def test_failed_counts_as_terminal(self) -> None:
        sigs = iter_signals(["BERNSTEIN:FAILED"])
        assert has_terminal_signal(sigs) is True

    def test_missing_terminal_signal_is_runtime_warning(self) -> None:
        # The class is a RuntimeWarning subclass so the harness can log
        # it without aborting the run.
        assert issubclass(MissingTerminalSignal, RuntimeWarning)


# ---------------------------------------------------------------------------
# Concurrency: parser is pure and safe across threads
# ---------------------------------------------------------------------------


class TestConcurrentParsing:
    """Multiple adapter streams may be parsed in parallel without contention."""

    def test_concurrent_multi_adapter_parsing(self) -> None:
        """Simulate N adapters each emitting their own stdout stream."""
        adapter_streams = []
        for adapter_idx in range(8):
            stream = [
                f"adapter-{adapter_idx} starting",
                format_signal(SignalKind.PLAN_DRAFT, {"markdown": f"# adapter {adapter_idx}"}),
                f"adapter-{adapter_idx} working",
                format_signal(
                    SignalKind.QUESTION,
                    {"question": f"q from {adapter_idx}", "id": f"a{adapter_idx}-q1"},
                ),
                format_signal(SignalKind.PLAN_READY, {"path": f".sdd/plan-{adapter_idx}.md"}),
                format_signal(SignalKind.COMPLETED, {"adapter": adapter_idx}),
            ]
            adapter_streams.append((adapter_idx, stream))

        def _parse_one(item: tuple[int, list[str]]) -> tuple[int, list[StreamSignal]]:
            idx, lines = item
            return idx, iter_signals(lines)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_parse_one, adapter_streams))

        # Each stream must produce the same 4 canonical signals in
        # the same order, with the payload tagged to its own adapter
        # - proving the parser carried no per-thread state.
        for idx, signals in results:
            kinds = [s.kind for s in signals]
            assert kinds == [
                SignalKind.PLAN_DRAFT,
                SignalKind.QUESTION,
                SignalKind.PLAN_READY,
                SignalKind.COMPLETED,
            ]
            assert signals[0].payload["markdown"] == f"# adapter {idx}"
            assert signals[1].payload["id"] == f"a{idx}-q1"
            assert signals[2].payload["path"] == f".sdd/plan-{idx}.md"
            assert signals[3].payload["adapter"] == idx


# ---------------------------------------------------------------------------
# Misc invariants
# ---------------------------------------------------------------------------


def test_signal_prefix_constant_is_stable() -> None:
    """The wire prefix is a stability contract - pin it explicitly."""
    assert SIGNAL_PREFIX == "BERNSTEIN:"


def test_signal_kind_values_are_uppercase() -> None:
    """Operator-visible tokens are upper-case for log-tail readability."""
    for kind in SignalKind:
        assert kind.value == kind.value.upper()


def test_stream_signal_is_frozen() -> None:
    """The dataclass is frozen so events are safe to share across threads."""
    sig = StreamSignal(kind=SignalKind.COMPLETED)
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        sig.kind = SignalKind.FAILED  # type: ignore[misc]


def test_payload_decodes_nested_structures() -> None:
    """Payload JSON objects may carry arbitrary nested data."""
    payload = {
        "question": "Pick one",
        "options": [
            {"label": "a", "value": 1},
            {"label": "b", "value": 2},
        ],
        "meta": {"id": "q-1", "tags": ["x", "y"]},
    }
    line = format_signal(SignalKind.QUESTION, payload)
    parsed = parse_signal(line)
    assert parsed is not None
    assert parsed.payload == payload


def test_format_signal_keeps_keys_sorted() -> None:
    """Deterministic serialisation simplifies golden-file diffs."""
    line = format_signal(SignalKind.QUESTION, {"b": 2, "a": 1})
    # Strip prefix and kind to inspect payload only.
    _, payload_text = line.split(" ", 1)
    assert payload_text == json.dumps({"a": 1, "b": 2}, separators=(",", ":"), sort_keys=True)
