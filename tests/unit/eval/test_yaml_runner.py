"""Unit tests for :mod:`bernstein.eval.yaml_runner`.

The runner is pure-Python with file I/O at the edges, so the test suite
covers schema validation, golden-dataset scoring, judge integration,
threshold checks, persistence, listing, and diffing - all without
network access. The default :func:`mock_executor` keeps every test
deterministic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from bernstein.eval.yaml_runner import (
    EvalSpec,
    JudgeSpec,
    PromptOutcome,
    PromptSpec,
    ThresholdSpec,
    YAMLRunner,
    aggregate_adapter,
    check_thresholds,
    diff_runs,
    evaluate_golden,
    lineage_stub_for,
    list_runs,
    load_dataset_jsonl,
    load_spec,
    merge_prompts,
    save_report,
)

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_eval_spec_requires_adapters() -> None:
    """A spec with no adapters fails validation."""
    with pytest.raises(ValueError, match="adapters"):
        EvalSpec.model_validate(
            {
                "name": "noop",
                "adapters": [],
                "prompts": [{"id": "p1", "text": "hi"}],
            },
        )


def test_eval_spec_requires_prompts_or_dataset() -> None:
    """A spec without inline prompts and without a dataset is rejected."""
    with pytest.raises(ValueError, match="at least one prompt"):
        EvalSpec.model_validate({"name": "noop", "adapters": ["mock"]})


def test_eval_spec_rejects_unknown_fields() -> None:
    """``extra='forbid'`` blocks unknown fields (e.g. misspelled ``prompts``)."""
    with pytest.raises(ValueError):
        EvalSpec.model_validate(
            {
                "name": "typo",
                "adapters": ["mock"],
                "prompts_typo": [{"id": "p1", "text": "hi"}],
            },
        )


def test_prompt_spec_regex_must_compile() -> None:
    """An invalid regex is rejected at validation time."""
    with pytest.raises(ValueError, match="invalid expected_output_regex"):
        PromptSpec.model_validate({"id": "p", "text": "x", "expected_output_regex": "(unclosed"})


def test_threshold_spec_clamped_to_unit_interval() -> None:
    """Thresholds must live in ``[0.0, 1.0]``."""
    with pytest.raises(ValueError):
        ThresholdSpec.model_validate({"golden_pass_rate_min": 1.5})


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_load_spec_from_yaml(tmp_path: Path) -> None:
    """A valid YAML body parses into an :class:`EvalSpec`."""
    spec_path = tmp_path / "spec.yaml"
    _write_yaml(
        spec_path,
        {
            "name": "demo",
            "adapters": ["mock"],
            "prompts": [{"id": "p1", "text": "hello", "expected_output_contains": ["hello"]}],
        },
    )
    spec = load_spec(spec_path)
    assert spec.name == "demo"
    assert spec.adapters == ["mock"]
    assert spec.prompts[0].id == "p1"


def test_load_spec_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_spec(tmp_path / "nope.yaml")


def test_load_dataset_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines in the JSONL dataset are ignored."""
    data_path = tmp_path / "data.jsonl"
    data_path.write_text(
        '\n{"id": "p1", "text": "a"}\n\n{"id": "p2", "text": "b"}\n',
        encoding="utf-8",
    )
    prompts = load_dataset_jsonl(data_path)
    assert [p.id for p in prompts] == ["p1", "p2"]


def test_load_dataset_jsonl_invalid_json_message(tmp_path: Path) -> None:
    data_path = tmp_path / "data.jsonl"
    data_path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_dataset_jsonl(data_path)


def test_merge_prompts_detects_duplicate_ids(tmp_path: Path) -> None:
    """Duplicate ids across inline + dataset surface a clear error."""
    data_path = tmp_path / "data.jsonl"
    data_path.write_text('{"id": "p1", "text": "a"}\n', encoding="utf-8")
    spec = EvalSpec(
        name="dup",
        adapters=["mock"],
        prompts=[PromptSpec(id="p1", text="inline")],
        dataset=str(data_path),
    )
    with pytest.raises(ValueError, match="duplicate prompt ids"):
        merge_prompts(spec, base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Golden-dataset scoring
# ---------------------------------------------------------------------------


def test_evaluate_golden_pass_with_substring_and_regex() -> None:
    prompt = PromptSpec(
        id="p",
        text="t",
        expected_output_contains=["hello", "world"],
        expected_output_regex=r"hello.*world",
    )
    passed, reason = evaluate_golden(prompt, "say hello to the world")
    assert passed is True
    assert reason == ""


def test_evaluate_golden_fails_on_missing_substring() -> None:
    prompt = PromptSpec(id="p", text="t", expected_output_contains=["zzz"])
    passed, reason = evaluate_golden(prompt, "say hello")
    assert passed is False
    assert "missing substring" in reason


def test_evaluate_golden_fails_on_regex_miss() -> None:
    prompt = PromptSpec(id="p", text="t", expected_output_regex=r"^foo")
    passed, reason = evaluate_golden(prompt, "bar")
    assert passed is False
    assert "regex" in reason


# ---------------------------------------------------------------------------
# Aggregation + thresholds
# ---------------------------------------------------------------------------


def _outcome(adapter: str, prompt_id: str, *, golden: bool, judge: float | None = None) -> PromptOutcome:
    return PromptOutcome(
        prompt_id=prompt_id,
        adapter=adapter,
        output="x",
        golden_passed=golden,
        golden_reason="" if golden else "miss",
        judge_score=judge,
    )


def test_aggregate_adapter_no_judge_uses_golden_rate() -> None:
    outcomes = [
        _outcome("a", "p1", golden=True),
        _outcome("a", "p2", golden=False),
    ]
    agg = aggregate_adapter("a", outcomes, judge_weight=0.5)
    assert agg.prompt_count == 2
    assert agg.golden_passed == 1
    assert agg.golden_pass_rate == 0.5
    assert agg.judge_mean == 0.0
    assert agg.overall_score == 0.5


def test_aggregate_adapter_combines_judge_and_golden() -> None:
    outcomes = [
        _outcome("a", "p1", golden=True, judge=0.8),
        _outcome("a", "p2", golden=True, judge=0.6),
    ]
    agg = aggregate_adapter("a", outcomes, judge_weight=0.5)
    assert agg.golden_pass_rate == 1.0
    assert agg.judge_mean == pytest.approx(0.7)
    assert agg.overall_score == pytest.approx(0.5 * 1.0 + 0.5 * 0.7)


def test_aggregate_adapter_empty_outcomes() -> None:
    agg = aggregate_adapter("a", [], judge_weight=0.5)
    assert agg.prompt_count == 0
    assert agg.overall_score == 0.0


def test_check_thresholds_reports_per_adapter_failures() -> None:
    spec = EvalSpec(
        name="t",
        adapters=["a", "b"],
        prompts=[PromptSpec(id="p1", text="x")],
        thresholds=ThresholdSpec(golden_pass_rate_min=0.5, overall_score_min=0.5),
    )
    aggs = [
        aggregate_adapter(
            "a",
            [_outcome("a", "p1", golden=True)],
            judge_weight=0.0,
        ),
        aggregate_adapter(
            "b",
            [_outcome("b", "p1", golden=False)],
            judge_weight=0.0,
        ),
    ]
    passed, failures = check_thresholds(spec, aggs)
    assert passed is False
    joined = " | ".join(failures)
    assert "b: golden_pass_rate" in joined
    assert "b: overall_score" in joined
    assert "a: " not in joined


# ---------------------------------------------------------------------------
# YAMLRunner end-to-end
# ---------------------------------------------------------------------------


def test_runner_executes_mock_adapter_and_passes_golden() -> None:
    """Mock executor echoes the prompt text so substring assertions pass."""
    spec = EvalSpec(
        name="echo",
        adapters=["mock", "claude"],
        prompts=[
            PromptSpec(id="p1", text="hello-world", expected_output_contains=["hello-world"]),
            PromptSpec(id="p2", text="ping", expected_output_contains=["ping"]),
        ],
    )
    runner = YAMLRunner()
    report = runner.run(spec, base_dir=Path.cwd())
    assert report.spec_name == "echo"
    assert len(report.outcomes) == 4  # 2 prompts x 2 adapters
    assert {a.adapter for a in report.per_adapter} == {"mock", "claude"}
    assert all(a.golden_pass_rate == 1.0 for a in report.per_adapter)
    assert report.thresholds_passed is True


def test_runner_threshold_failure_surfaces_in_report() -> None:
    spec = EvalSpec(
        name="strict",
        adapters=["mock"],
        prompts=[
            PromptSpec(id="p1", text="hi", expected_output_contains=["unreachable-needle"]),
        ],
        thresholds=ThresholdSpec(golden_pass_rate_min=1.0),
    )
    runner = YAMLRunner()
    report = runner.run(spec, base_dir=Path.cwd())
    assert report.thresholds_passed is False
    assert any("golden_pass_rate" in f for f in report.threshold_failures)


def test_runner_invokes_judge_when_configured() -> None:
    """The runner only calls the judge when ``judge_fn`` is provided."""
    spec = EvalSpec(
        name="judge",
        adapters=["mock"],
        prompts=[PromptSpec(id="p1", text="hello", expected_output_contains=["hello"])],
        judge=JudgeSpec(rubric="be terse", weight=0.5),
    )

    call_log: list[tuple[str, str]] = []

    def stub_judge(_judge_spec: JudgeSpec, prompt: PromptSpec, output: str) -> float:
        call_log.append((prompt.id, output))
        return 0.8

    runner = YAMLRunner(judge_fn=stub_judge)
    report = runner.run(spec, base_dir=Path.cwd())
    assert call_log == [("p1", "[mock] hello")]
    assert report.outcomes[0].judge_score == pytest.approx(0.8)


def test_runner_skips_judge_when_judge_fn_missing() -> None:
    """When the spec declares a judge but no callable is injected, judge runs are skipped."""
    spec = EvalSpec(
        name="no-judge-fn",
        adapters=["mock"],
        prompts=[PromptSpec(id="p1", text="hello", expected_output_contains=["hello"])],
        judge=JudgeSpec(weight=1.0),
    )
    runner = YAMLRunner()
    report = runner.run(spec, base_dir=Path.cwd())
    assert all(o.judge_score is None for o in report.outcomes)


def test_runner_uses_custom_executor() -> None:
    """A custom executor short-circuits the mock echo."""

    def fixed_executor(adapter: str, prompt: PromptSpec) -> str:
        return f"{adapter}/{prompt.id}=ok"

    spec = EvalSpec(
        name="custom-exec",
        adapters=["a"],
        prompts=[PromptSpec(id="p1", text="ignored", expected_output_contains=["a/p1=ok"])],
    )
    runner = YAMLRunner(executor=fixed_executor)
    report = runner.run(spec, base_dir=Path.cwd())
    assert report.outcomes[0].output == "a/p1=ok"
    assert report.outcomes[0].golden_passed is True


# ---------------------------------------------------------------------------
# Persistence + listing
# ---------------------------------------------------------------------------


def _trivial_spec() -> EvalSpec:
    return EvalSpec(
        name="persist-test",
        adapters=["mock"],
        prompts=[PromptSpec(id="p1", text="hello", expected_output_contains=["hello"])],
    )


def test_save_report_writes_json_and_markdown(tmp_path: Path) -> None:
    spec = _trivial_spec()
    runner = YAMLRunner()
    report = runner.run(spec, base_dir=tmp_path)
    json_path, md_path = save_report(report, state_dir=tmp_path)
    assert json_path.exists()
    assert md_path is not None and md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["spec_name"] == "persist-test"
    assert payload["per_adapter"][0]["adapter"] == "mock"
    md = md_path.read_text(encoding="utf-8")
    assert "Eval report: persist-test" in md
    assert "Per-adapter summary" in md


def test_save_report_can_skip_markdown(tmp_path: Path) -> None:
    spec = _trivial_spec()
    report = YAMLRunner().run(spec, base_dir=tmp_path)
    json_path, md_path = save_report(report, state_dir=tmp_path, write_markdown=False)
    assert json_path.exists()
    assert md_path is None


def test_list_runs_returns_newest_first(tmp_path: Path) -> None:
    spec = _trivial_spec()
    runner = YAMLRunner()
    a, _ = save_report(runner.run(spec, base_dir=tmp_path), state_dir=tmp_path)
    # Tiny mtime bump so sort is stable across filesystems.
    import os

    os.utime(a, (a.stat().st_atime, a.stat().st_mtime - 5))
    b, _ = save_report(runner.run(spec, base_dir=tmp_path), state_dir=tmp_path)
    runs = list_runs(tmp_path)
    assert runs[0] == b
    assert a in runs


def test_list_runs_empty_state_dir(tmp_path: Path) -> None:
    assert list_runs(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# Lineage stub
# ---------------------------------------------------------------------------


def test_lineage_stub_hash_matches_content(tmp_path: Path) -> None:
    """Lineage stub records a sha256 over the run file bytes."""
    import hashlib

    spec = _trivial_spec()
    report = YAMLRunner().run(spec, base_dir=tmp_path)
    json_path, _ = save_report(report, state_dir=tmp_path)
    stub = lineage_stub_for(json_path, lineage_tag=spec.lineage_tag)
    digest = "sha256:" + hashlib.sha256(json_path.read_bytes()).hexdigest()
    assert stub.content_hash == digest
    assert stub.lineage_tag == spec.lineage_tag
    payload = stub.to_dict()
    assert payload["artefact_path"] == str(json_path)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _run_with_executor(tmp_path: Path, executor) -> Path:
    spec = EvalSpec(
        name="diffable",
        adapters=["mock"],
        prompts=[
            PromptSpec(id="p1", text="ping", expected_output_contains=["ok"]),
        ],
    )
    runner = YAMLRunner(executor=executor)
    report = runner.run(spec, base_dir=tmp_path)
    json_path, _ = save_report(report, state_dir=tmp_path)
    return json_path


def test_diff_runs_detects_improvement(tmp_path: Path) -> None:
    """When run B has higher overall score, ``winner == 'b'``."""

    def fail_executor(adapter: str, prompt: PromptSpec) -> str:
        return "no match"

    def pass_executor(adapter: str, prompt: PromptSpec) -> str:
        return "ok"

    state_a = tmp_path / "a"
    state_b = tmp_path / "b"
    path_a = _run_with_executor(state_a, fail_executor)
    path_b = _run_with_executor(state_b, pass_executor)

    diff = diff_runs(path_a, path_b)
    assert diff.winner == "b"
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.adapter == "mock"
    assert entry.overall_a == pytest.approx(0.0)
    assert entry.overall_b == pytest.approx(1.0)
    assert entry.overall_delta == pytest.approx(1.0)


def test_diff_runs_declares_tie_inside_tolerance(tmp_path: Path) -> None:
    """Runs with identical scores fall inside the tolerance band."""

    def pass_executor(adapter: str, prompt: PromptSpec) -> str:
        return "ok"

    state_a = tmp_path / "a"
    state_b = tmp_path / "b"
    path_a = _run_with_executor(state_a, pass_executor)
    path_b = _run_with_executor(state_b, pass_executor)
    diff = diff_runs(path_a, path_b, tolerance=0.05)
    assert diff.winner == "tie"


def test_diff_runs_handles_adapter_present_only_in_one_run(tmp_path: Path) -> None:
    """Adapters missing from one run get zero-baselined."""

    def pass_executor(adapter: str, prompt: PromptSpec) -> str:
        return "ok"

    spec_a = EvalSpec(
        name="only-a",
        adapters=["mock"],
        prompts=[PromptSpec(id="p1", text="ping", expected_output_contains=["ok"])],
    )
    spec_b = EvalSpec(
        name="only-b",
        adapters=["mock", "extra"],
        prompts=[PromptSpec(id="p1", text="ping", expected_output_contains=["ok"])],
    )
    state_a = tmp_path / "a"
    state_b = tmp_path / "b"
    report_a = YAMLRunner(executor=pass_executor).run(spec_a, base_dir=state_a)
    report_b = YAMLRunner(executor=pass_executor).run(spec_b, base_dir=state_b)
    path_a, _ = save_report(report_a, state_dir=state_a)
    path_b, _ = save_report(report_b, state_dir=state_b)
    diff = diff_runs(path_a, path_b)
    adapters = {e.adapter for e in diff.entries}
    assert adapters == {"mock", "extra"}
    extra_entry = next(e for e in diff.entries if e.adapter == "extra")
    assert extra_entry.overall_a == 0.0
    assert extra_entry.overall_b == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Round-trip: spec on disk -> run -> persist -> diff
# ---------------------------------------------------------------------------


def test_full_round_trip(tmp_path: Path) -> None:
    """A spec on disk runs through to a diff-ready JSON artefact."""
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text(
        '{"id": "d1", "text": "ping", "expected_output_contains": ["ping"]}\n',
        encoding="utf-8",
    )
    spec_path = tmp_path / "spec.yaml"
    _write_yaml(
        spec_path,
        {
            "name": "round-trip",
            "lineage_tag": "rt-tag",
            "dataset": "ds.jsonl",
            "adapters": ["mock"],
            "prompts": [
                {"id": "p1", "text": "hello", "expected_output_contains": ["hello"]},
            ],
            "thresholds": {"golden_pass_rate_min": 1.0},
        },
    )

    spec = load_spec(spec_path)
    runner = YAMLRunner()
    report = runner.run(spec, base_dir=spec_path.parent)
    assert report.thresholds_passed is True
    assert {o.prompt_id for o in report.outcomes} == {"p1", "d1"}

    json_path, md_path = save_report(report, state_dir=tmp_path)
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["lineage_tag"] == "rt-tag"
    assert md_path is not None
    assert re.search(r"\| mock \|", md_path.read_text(encoding="utf-8"))
