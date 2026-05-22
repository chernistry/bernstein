"""Tests for ``claude_cost_tracking`` - parse Claude Code session output.

Covers the dark paths of the session-output parser and the aggregator:

* ``SessionCostData`` token totals + JSON serialisation.
* ``parse_session_output`` across all three input shapes (stream-json
  events, text summary lines, structured result JSON), including the
  ``max`` accumulation semantics and comma-stripped integers.
* ``_extract_from_json`` field precedence (cost keys, duration units,
  cache-read / cache-write alias keys, model fill-in).
* ``CostTrackingAggregator`` per-model rollups, dedupe-by-session, and
  the rounded summary contract.

Every assertion pins an observed numeric / structural fact so a
behaviour change would break the test.
"""

from __future__ import annotations

import json

from bernstein.core.cost.claude_cost_tracking import (
    CostTrackingAggregator,
    SessionCostData,
    parse_session_output,
)


class TestSessionCostData:
    def test_total_tokens_sums_all_four_buckets(self) -> None:
        data = SessionCostData(
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=3,
            cache_write_tokens=7,
        )
        assert data.total_tokens == 40

    def test_total_tokens_zero_by_default(self) -> None:
        assert SessionCostData().total_tokens == 0

    def test_to_dict_rounds_cost_to_six_places(self) -> None:
        data = SessionCostData(total_cost_usd=0.123456789)
        assert data.to_dict()["total_cost_usd"] == 0.123457

    def test_to_dict_rounds_duration_to_one_place(self) -> None:
        data = SessionCostData(duration_s=12.349)
        assert data.to_dict()["duration_s"] == 12.3

    def test_to_dict_includes_derived_total_tokens(self) -> None:
        data = SessionCostData(input_tokens=5, output_tokens=6)
        d = data.to_dict()
        assert d["total_tokens"] == 11
        assert d["input_tokens"] == 5
        assert d["output_tokens"] == 6

    def test_to_dict_is_json_serialisable(self) -> None:
        data = SessionCostData(session_id="s1", model="sonnet", total_cost_usd=1.5)
        # round-trips through JSON without raising
        round_tripped = json.loads(json.dumps(data.to_dict()))
        assert round_tripped["session_id"] == "s1"
        assert round_tripped["model"] == "sonnet"


class TestParseSessionOutputText:
    def test_parses_total_cost_line(self) -> None:
        out = "some preamble\nTotal cost: $0.42\nmore"
        data = parse_session_output(out)
        assert data.total_cost_usd == 0.42

    def test_parses_session_cost_line_case_insensitive(self) -> None:
        data = parse_session_output("session COST: $1.23")
        assert data.total_cost_usd == 1.23

    def test_cost_line_without_dollar_sign(self) -> None:
        data = parse_session_output("Total cost: 0.10")
        assert data.total_cost_usd == 0.10

    def test_parses_token_line_with_commas(self) -> None:
        data = parse_session_output("Input tokens: 12,345, Output tokens: 6,789")
        assert data.input_tokens == 12345
        assert data.output_tokens == 6789

    def test_prompt_completion_synonyms(self) -> None:
        data = parse_session_output("Prompt tokens: 100 ... Completion tokens: 200")
        assert data.input_tokens == 100
        assert data.output_tokens == 200

    def test_cost_takes_running_maximum(self) -> None:
        # Two cost lines; parser keeps the larger value.
        out = "Total cost: $0.10\nTotal cost: $0.99\nTotal cost: $0.50"
        data = parse_session_output(out)
        assert data.total_cost_usd == 0.99

    def test_blank_lines_skipped(self) -> None:
        data = parse_session_output("\n\n   \nTotal cost: $0.05\n\n")
        assert data.total_cost_usd == 0.05

    def test_no_cost_data_yields_zero(self) -> None:
        data = parse_session_output("nothing relevant here\njust prose")
        assert data.total_cost_usd == 0.0
        assert data.total_tokens == 0

    def test_session_id_and_model_passthrough(self) -> None:
        data = parse_session_output("", session_id="sess-9", model="opus")
        assert data.session_id == "sess-9"
        assert data.model == "opus"


class TestParseSessionOutputJson:
    def test_extracts_usage_dict_tokens(self) -> None:
        line = json.dumps({"usage": {"input_tokens": 500, "output_tokens": 250}})
        data = parse_session_output(line)
        assert data.input_tokens == 500
        assert data.output_tokens == 250

    def test_extracts_cache_tokens_primary_keys(self) -> None:
        line = json.dumps(
            {
                "usage": {
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 40,
                }
            }
        )
        data = parse_session_output(line)
        assert data.cache_read_tokens == 80
        assert data.cache_write_tokens == 40

    def test_extracts_cache_tokens_alias_keys(self) -> None:
        line = json.dumps({"usage": {"cache_read_tokens": 11, "cache_write_tokens": 22}})
        data = parse_session_output(line)
        assert data.cache_read_tokens == 11
        assert data.cache_write_tokens == 22

    def test_extracts_cost_usd_key(self) -> None:
        data = parse_session_output(json.dumps({"cost_usd": 3.14}))
        assert data.total_cost_usd == 3.14

    def test_extracts_session_cost_key(self) -> None:
        data = parse_session_output(json.dumps({"session_cost": 7.5}))
        assert data.total_cost_usd == 7.5

    def test_duration_ms_converted_to_seconds(self) -> None:
        data = parse_session_output(json.dumps({"duration_ms": 4200}))
        assert data.duration_s == 4.2

    def test_duration_s_kept_as_is(self) -> None:
        data = parse_session_output(json.dumps({"duration_s": 9.0}))
        assert data.duration_s == 9.0

    def test_elapsed_ms_converted(self) -> None:
        data = parse_session_output(json.dumps({"elapsed_ms": 1500}))
        assert data.duration_s == 1.5

    def test_model_filled_from_json_when_unset(self) -> None:
        data = parse_session_output(json.dumps({"model": "claude-sonnet-4"}))
        assert data.model == "claude-sonnet-4"

    def test_explicit_model_not_overwritten_by_json(self) -> None:
        # Caller passes a model; a JSON model field must not clobber it.
        data = parse_session_output(json.dumps({"model": "haiku"}), model="opus")
        assert data.model == "opus"

    def test_assistant_type_counts_turns(self) -> None:
        out = "\n".join(
            json.dumps(obj)
            for obj in (
                {"type": "assistant", "usage": {"input_tokens": 1, "output_tokens": 1}},
                {"type": "assistant", "usage": {"input_tokens": 2, "output_tokens": 2}},
                {"type": "user"},
            )
        )
        data = parse_session_output(out)
        assert data.turns == 2

    def test_assistant_role_also_counts_turns(self) -> None:
        out = json.dumps({"role": "assistant", "usage": {"input_tokens": 1, "output_tokens": 1}})
        data = parse_session_output(out)
        assert data.turns == 1

    def test_usage_max_accumulation_across_events(self) -> None:
        # The parser keeps the maximum seen per field, not the sum.
        out = "\n".join(json.dumps({"usage": {"input_tokens": v, "output_tokens": v * 2}}) for v in (10, 100, 50))
        data = parse_session_output(out)
        assert data.input_tokens == 100
        assert data.output_tokens == 200

    def test_non_dict_json_falls_through_to_text(self) -> None:
        # A bare JSON array is valid JSON but not a dict; the line then
        # still gets a regex pass (which finds nothing here).
        data = parse_session_output("[1, 2, 3]")
        assert data.total_tokens == 0
        assert data.total_cost_usd == 0.0

    def test_mixed_json_and_text_lines(self) -> None:
        out = "\n".join(
            [
                json.dumps({"usage": {"input_tokens": 300, "output_tokens": 150}}),
                "Total cost: $2.00",
            ]
        )
        data = parse_session_output(out)
        assert data.input_tokens == 300
        assert data.output_tokens == 150
        assert data.total_cost_usd == 2.0

    def test_invalid_cost_value_in_json_ignored(self) -> None:
        # cost field present but non-numeric: suppressed, stays at 0.
        data = parse_session_output(json.dumps({"cost": "not-a-number"}))
        assert data.total_cost_usd == 0.0

    def test_non_dict_usage_value_skipped(self) -> None:
        # ``usage`` present but not a dict: the usage branch is skipped,
        # other fields (cost) still parse.
        data = parse_session_output(json.dumps({"usage": "oops", "cost_usd": 1.0}))
        assert data.total_tokens == 0
        assert data.total_cost_usd == 1.0


class TestCostTrackingAggregator:
    def test_record_and_total_cost(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", total_cost_usd=1.0))
        agg.record_session(SessionCostData(session_id="b", total_cost_usd=2.5))
        assert agg.total_cost_usd() == 3.5

    def test_record_same_session_id_overwrites(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="x", total_cost_usd=1.0))
        agg.record_session(SessionCostData(session_id="x", total_cost_usd=9.0))
        # Same key replaces; total reflects only the latest record.
        assert agg.total_cost_usd() == 9.0
        assert len(agg.sessions) == 1

    def test_total_tokens_across_sessions(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", input_tokens=10, output_tokens=5))
        agg.record_session(SessionCostData(session_id="b", input_tokens=20, cache_read_tokens=2))
        assert agg.total_tokens() == 37

    def test_empty_aggregator_totals_zero(self) -> None:
        agg = CostTrackingAggregator()
        assert agg.total_cost_usd() == 0.0
        assert agg.total_tokens() == 0

    def test_summary_groups_by_model(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", model="sonnet", total_cost_usd=1.0, input_tokens=100))
        agg.record_session(SessionCostData(session_id="b", model="sonnet", total_cost_usd=2.0, input_tokens=50))
        agg.record_session(SessionCostData(session_id="c", model="opus", total_cost_usd=4.0, output_tokens=10))
        summary = agg.summary()
        assert summary["total_sessions"] == 3
        assert summary["by_model"]["sonnet"]["sessions"] == 2
        assert summary["by_model"]["sonnet"]["cost_usd"] == 3.0
        assert summary["by_model"]["sonnet"]["tokens"] == 150
        assert summary["by_model"]["opus"]["cost_usd"] == 4.0
        assert summary["by_model"]["opus"]["sessions"] == 1

    def test_summary_unknown_model_bucket(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", model="", total_cost_usd=1.0))
        summary = agg.summary()
        assert "unknown" in summary["by_model"]
        assert summary["by_model"]["unknown"]["cost_usd"] == 1.0

    def test_summary_rounds_floats(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", model="m", total_cost_usd=0.1234567))
        summary = agg.summary()
        assert summary["total_cost_usd"] == 0.123457
        assert summary["by_model"]["m"]["cost_usd"] == 0.123457

    def test_summary_total_cost_matches_sum(self) -> None:
        agg = CostTrackingAggregator()
        agg.record_session(SessionCostData(session_id="a", total_cost_usd=1.5))
        agg.record_session(SessionCostData(session_id="b", total_cost_usd=2.5))
        summary = agg.summary()
        assert summary["total_cost_usd"] == 4.0
        assert summary["total_tokens"] == 0
