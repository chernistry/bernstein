"""Tests for the keep-failed-actions formatter (Manus harness pattern #5)."""

from __future__ import annotations

import pytest

from bernstein.core.tokens.compaction_pipeline import CompactionPipeline
from bernstein.core.tokens.failed_action_retention import (
    DEFAULT_HALF_LIFE_TURNS,
    RETAINED_PREFIX,
    FailedActionBlock,
    block_from_dict,
    filter_stale_blocks,
    split_retained_blocks,
    tag_failed_actions,
)


class TestTagFailedActions:
    def test_empty_input_returns_empty_string(self) -> None:
        assert tag_failed_actions([]) == ""

    def test_renders_block_with_marker_and_metadata(self) -> None:
        block = FailedActionBlock(
            tool_name="run_tests",
            error_text="AssertionError: expected 1 got 2",
            turn_index=3,
        )
        out = tag_failed_actions([block], current_turn=3)
        assert RETAINED_PREFIX in out
        assert "tool=run_tests" in out
        assert "turn=3" in out
        assert "staleness=0" in out
        assert "AssertionError" in out

    def test_staleness_increments_after_half_life(self) -> None:
        block = FailedActionBlock(tool_name="x", error_text="err", turn_index=0)
        # 6 turns later should still be staleness=1 with default half-life=6.
        out = tag_failed_actions([block], current_turn=6)
        assert "staleness=1" in out
        # 13 turns later → staleness=2.
        out2 = tag_failed_actions([block], current_turn=13)
        assert "staleness=2" in out2

    def test_empty_error_text_substitutes_placeholder(self) -> None:
        block = FailedActionBlock(tool_name="x", error_text="", turn_index=0)
        out = tag_failed_actions([block])
        assert "(no stderr captured)" in out

    def test_multiple_blocks_separated_by_blank_line(self) -> None:
        blocks = [
            FailedActionBlock("a", "err-a", 0),
            FailedActionBlock("b", "err-b", 1),
        ]
        out = tag_failed_actions(blocks, current_turn=1)
        assert out.count(RETAINED_PREFIX) == 2
        assert "\n\n" in out

    def test_invalid_half_life_raises(self) -> None:
        with pytest.raises(ValueError):
            tag_failed_actions([FailedActionBlock("x", "y", 0)], half_life_turns=0)


class TestSplitRetainedBlocks:
    def test_no_retained_returns_input_unchanged(self) -> None:
        text = "regular scrollback line 1\nline 2"
        non_retained, retained = split_retained_blocks(text)
        assert non_retained == text
        assert retained == []

    def test_extracts_single_retained_block(self) -> None:
        retained_text = f"{RETAINED_PREFIX} tool=t turn=0 staleness=0\nstderr: boom\n\nregular line"
        non_retained, retained = split_retained_blocks(retained_text)
        assert len(retained) == 1
        assert retained[0].startswith(RETAINED_PREFIX)
        assert "stderr: boom" in retained[0]
        assert RETAINED_PREFIX not in non_retained
        assert "regular line" in non_retained

    def test_round_trip_through_pipeline_preserves_block(self) -> None:
        """Carved blocks survive media-stripping + summary verbatim."""
        retained = f"{RETAINED_PREFIX} tool=run_tests turn=0 staleness=0\nTraceback: boom"
        original = f"## context header\nsome prose\n\n{retained}\n\nmore prose"
        pipeline = CompactionPipeline(plugin_manager=None)
        result = pipeline.execute(
            session_id="s1",
            context_text=original,
            tokens_before=100,
            keep_failed_actions=True,
        )
        assert RETAINED_PREFIX in result.compacted_text
        assert "Traceback: boom" in result.compacted_text


class TestFilterStaleBlocks:
    def test_keeps_recent_blocks(self) -> None:
        blocks = [
            FailedActionBlock("x", "e", turn_index=10),
            FailedActionBlock("y", "e", turn_index=11),
        ]
        kept = filter_stale_blocks(blocks, current_turn=12)
        assert len(kept) == 2

    def test_drops_blocks_past_max_staleness(self) -> None:
        blocks = [
            FailedActionBlock("old", "e", turn_index=0),
            FailedActionBlock("recent", "e", turn_index=20),
        ]
        # current_turn=20, half_life=6 → old has staleness=3, recent has 0.
        kept = filter_stale_blocks(
            blocks,
            current_turn=20,
            half_life_turns=DEFAULT_HALF_LIFE_TURNS,
            max_staleness=2,
        )
        names = [b.tool_name for b in kept]
        assert names == ["recent"]

    def test_invalid_args_raise(self) -> None:
        with pytest.raises(ValueError):
            filter_stale_blocks([], current_turn=0, half_life_turns=0)
        with pytest.raises(ValueError):
            filter_stale_blocks([], current_turn=0, max_staleness=-1)


class TestBlockFromDict:
    def test_round_trip(self) -> None:
        b = block_from_dict({"tool_name": "t", "error_text": "e", "turn_index": 4})
        assert b == FailedActionBlock("t", "e", 4)

    def test_missing_required_key_raises(self) -> None:
        with pytest.raises(KeyError):
            block_from_dict({"tool_name": "t"})

    def test_default_turn_index_zero(self) -> None:
        b = block_from_dict({"tool_name": "t", "error_text": "e"})
        assert b.turn_index == 0


class TestPipelineKeepFailedActionsFlag:
    def test_disabled_by_default_strips_failure_marker(self) -> None:
        """When the flag is off the pipeline behaves exactly as before."""
        text = f"header\n\n{RETAINED_PREFIX} tool=t turn=0 staleness=0\nerr-body\n\nfooter"
        pipeline = CompactionPipeline(plugin_manager=None)
        result = pipeline.execute(
            session_id="s",
            context_text=text,
            tokens_before=100,
            # keep_failed_actions defaults to False
        )
        # With the flag off, the deterministic summary path doesn't preserve
        # the marker - proving disabled-vs-enabled have observable
        # difference.
        assert RETAINED_PREFIX not in result.compacted_text

    def test_enabled_appends_explicit_retained_failures(self) -> None:
        pipeline = CompactionPipeline(plugin_manager=None)
        explicit = [FailedActionBlock("run_tests", "boom", turn_index=0)]
        result = pipeline.execute(
            session_id="s",
            context_text="some plain context",
            tokens_before=100,
            keep_failed_actions=True,
            retained_failures=explicit,
            current_turn=0,
        )
        assert RETAINED_PREFIX in result.compacted_text
        assert "tool=run_tests" in result.compacted_text
        assert "boom" in result.compacted_text
