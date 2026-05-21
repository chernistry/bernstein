"""Integration tests: synthetic scenario generator → eval harness pickup.

Each test wires the generator end-to-end and asserts the *full* pipeline:
trace files in ``.sdd/traces/`` produce YAML cases in the output
directory, and the eval harness happily loads those YAMLs as if they
were hand-authored. The integration boundary is interesting because the
incident-eval harness reads YAML files we emit, and any schema drift
between the generator and the harness would surface here.

These are integration-marked tests but stay offline - no network, no
real LLM calls. The Click ``CliRunner`` exercises the public CLI
surface so a future packaging refactor that breaks command registration
is caught here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from bernstein.eval.scenario_generator import (
    DEFAULT_OUT_DIR,
    DISABLE_ENV,
    case_to_yaml,
    generate_from_traces,
    materialise,
    write_cases,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_trace(traces_dir: Path, name: str, records: list[dict[str, object]]) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r) for r in records) + "\n"
    (traces_dir / name).write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Full traces → scenarios → on-disk YAML pipeline
# ---------------------------------------------------------------------------


def test_full_traces_to_yaml_pipeline(tmp_path: Path) -> None:
    """A populated trace dir produces YAML files matching the registry."""
    traces = tmp_path / ".sdd" / "traces"
    _seed_trace(
        traces,
        "001.jsonl",
        [
            {"tag": "large_diff", "task_id": "T-1"},
            {"tag": "cost_spike", "task_id": "T-2"},
            {"event": "noise"},
        ],
    )
    out_dir = tmp_path / "eval" / "golden_data" / "synthetic"

    result = generate_from_traces(
        workdir=tmp_path,
        out_dir=out_dir,
        from_traces=5,
        seed=42,
    )

    scenarios_in_corpus = {c.scenario for c in result.created}
    assert {"large_diff", "cost_spike"} <= scenarios_in_corpus

    # Every case is on disk and parses cleanly.
    emitted_files = sorted(out_dir.glob("syn-*.yaml"))
    assert len(emitted_files) == len(result.created)
    for path in emitted_files:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["source"] == "synthetic"
        assert loaded["id"] == path.stem


# ---------------------------------------------------------------------------
# 2. Re-running over the same trace corpus is idempotent on disk
# ---------------------------------------------------------------------------


def test_idempotent_against_existing_corpus(tmp_path: Path) -> None:
    """Re-running with the same seed must not duplicate files on disk."""
    traces = tmp_path / ".sdd" / "traces"
    _seed_trace(traces, "001.jsonl", [{"tag": "large_diff"}, {"tag": "flaky"}])

    r1 = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
    files_after_first = sorted((tmp_path / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))
    r2 = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
    files_after_second = sorted((tmp_path / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))

    assert len(r1.created) >= 1
    assert r2.created == []
    assert r2.skipped_duplicates >= len(r1.created)
    assert files_after_first == files_after_second


# ---------------------------------------------------------------------------
# 3. CLI surface end-to-end
# ---------------------------------------------------------------------------


def test_cli_generate_scenarios_writes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``bernstein eval generate-scenarios`` Click command emits YAML."""
    # The CLI uses ``Path(".")``. We chdir so the output goes under tmp.
    monkeypatch.chdir(tmp_path)
    traces = tmp_path / ".sdd" / "traces"
    _seed_trace(traces, "001.jsonl", [{"tag": "large_diff"}])

    from bernstein.cli.commands.eval_benchmark_cmd import eval_group

    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["generate-scenarios", "--from-traces", "3", "--seed", "7"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    files = list((tmp_path / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))
    assert files


def test_cli_synth_generate_explicit_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``bernstein eval synth-generate --scenario flaky_tests`` writes N YAMLs."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"

    from bernstein.cli.commands.eval_benchmark_cmd import eval_group

    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        [
            "synth-generate",
            "--scenario",
            "flaky_tests",
            "--params",
            "flake_rate=0.3",
            "--count",
            "3",
            "--seed",
            "42",
            "--out",
            str(out_dir),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    files = sorted(out_dir.glob("syn-*.yaml"))
    assert len(files) == 3
    for path in files:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["scenario"] == "flaky_tests"
        assert loaded["params"]["flake_rate"] == pytest.approx(0.3)


def test_cli_synth_list_lists_six(monkeypatch: pytest.MonkeyPatch) -> None:
    """``bernstein eval synth-list`` describes every registered scenario."""
    from bernstein.cli.commands.eval_benchmark_cmd import eval_group

    runner = CliRunner()
    result = runner.invoke(eval_group, ["synth-list"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    for scenario_id in (
        "large_diff",
        "slow_adapter",
        "flaky_tests",
        "racing_workers",
        "prompt_injection",
        "cost_spike",
    ):
        assert scenario_id in result.output


# ---------------------------------------------------------------------------
# 4. Disable switch fences the CLI
# ---------------------------------------------------------------------------


def test_cli_disable_switch_short_circuits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(DISABLE_ENV, "1")

    from bernstein.cli.commands.eval_benchmark_cmd import eval_group

    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["synth-generate", "--scenario", "large_diff", "--count", "3"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "disabled" in result.output.lower()
    assert not list((tmp_path / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))


# ---------------------------------------------------------------------------
# 5. Emitted cases are picked up by an existing-style eval-case directory
# ---------------------------------------------------------------------------


def test_emitted_cases_match_incident_eval_case_schema(tmp_path: Path) -> None:
    """The synthetic case YAML carries every key the incident harness reads.

    The incident harness scans the directory for ``inc-*.yaml`` files -
    we deliberately use a ``syn-*.yaml`` prefix to avoid collisions -
    but the wire format (id / severity / prompt / expected_outcome /
    source) must remain compatible so a future shared loader can ingest
    both.
    """
    cases = materialise("prompt_injection", count=2, seed=42)
    out = tmp_path / "synthetic"
    paths = write_cases(cases, out)
    assert len(paths) == 2

    for path in paths:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in ("id", "severity", "prompt", "expected_outcome", "source"):
            assert key in loaded, f"missing key {key!r} in {path}"
        assert loaded["severity"] == "P0"
        assert loaded["source"] == "synthetic"
        assert loaded["prompt"].strip()


# ---------------------------------------------------------------------------
# 6. Mixed trace shape: events, tags, categories all detected
# ---------------------------------------------------------------------------


def test_mixed_trace_record_shapes_detected(tmp_path: Path) -> None:
    traces = tmp_path / ".sdd" / "traces"
    _seed_trace(
        traces,
        "001.jsonl",
        [
            {"tag": "large_diff"},
            {"event": "race_condition"},
            {"category": "cost_spike"},
            {"tags": ["prompt_injection"]},
        ],
    )
    result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
    scenarios = {c.scenario for c in result.created}
    assert {
        "large_diff",
        "racing_workers",
        "cost_spike",
        "prompt_injection",
    } <= scenarios


# ---------------------------------------------------------------------------
# 7. Cap from-traces N - only the most recent files are considered
# ---------------------------------------------------------------------------


def test_only_n_most_recent_traces_considered(tmp_path: Path) -> None:
    import time as _time

    traces = tmp_path / ".sdd" / "traces"
    traces.mkdir(parents=True)
    # Three traces, only the newest two should be picked.
    _seed_trace(traces, "01.jsonl", [{"tag": "cost_spike"}])
    _time.sleep(0.02)
    _seed_trace(traces, "02.jsonl", [{"tag": "large_diff"}])
    _time.sleep(0.02)
    _seed_trace(traces, "03.jsonl", [{"tag": "flaky"}])

    result = generate_from_traces(workdir=tmp_path, from_traces=2, seed=42)
    scenarios = {c.scenario for c in result.created}
    # Newest two = flaky + large_diff. cost_spike is filtered out.
    assert "flaky_tests" in scenarios
    assert "large_diff" in scenarios
    assert "cost_spike" not in scenarios


# ---------------------------------------------------------------------------
# 8. Determinism observed at the filesystem layer
# ---------------------------------------------------------------------------


def test_deterministic_file_set_across_runs(tmp_path: Path) -> None:
    """Two clean workdirs with the same traces produce identical filenames."""
    other = tmp_path / "other"
    here = tmp_path / "here"

    for root in (here, other):
        traces = root / ".sdd" / "traces"
        _seed_trace(traces, "001.jsonl", [{"tag": "large_diff"}, {"tag": "flaky"}])
        generate_from_traces(workdir=root, from_traces=5, seed=42)

    here_files = sorted(p.name for p in (here / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))
    other_files = sorted(p.name for p in (other / Path(*DEFAULT_OUT_DIR)).glob("syn-*.yaml"))
    assert here_files == other_files
    assert here_files  # non-empty


# ---------------------------------------------------------------------------
# 9. CLI run does not require network - no live LLM is touched.
# ---------------------------------------------------------------------------


def test_no_network_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity: invoking the CLI does not import or call any HTTP client.

    We replace ``urllib.request.urlopen`` with a poison so any accidental
    network call would explode loudly. The CLI must succeed regardless.
    """
    import urllib.request

    def _poison(*args: object, **kwargs: object) -> object:
        raise RuntimeError("no network calls allowed in synthetic generator")

    monkeypatch.setattr(urllib.request, "urlopen", _poison)
    monkeypatch.chdir(tmp_path)

    from bernstein.cli.commands.eval_benchmark_cmd import eval_group

    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["synth-generate", "--scenario", "large_diff", "--count", "1", "--seed", "1"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 10. End-to-end: trace → generate → YAML inspectable by existing harness
# ---------------------------------------------------------------------------


def test_generated_case_yaml_resembles_incident_eval_case(tmp_path: Path) -> None:
    """Emit a case via the generator and re-emit via the same encoder.

    The encoded text is byte-identical so external readers (the eval
    harness, dashboards) get a stable wire format.
    """
    cases = materialise("racing_workers", count=1, seed=42)
    case = cases[0]
    text = case_to_yaml(case)
    loaded = yaml.safe_load(text)
    re_emitted = case_to_yaml(
        type(case)(
            id=loaded["id"],
            scenario=loaded["scenario"],
            severity=loaded["severity"],
            prompt=loaded["prompt"],
            expected_outcome=loaded["expected_outcome"],
            params=loaded["params"],
            tags=tuple(loaded["tags"]),
            source=loaded["source"],
            seed=loaded["seed"],
            created_at=loaded["created_at"],
        )
    )
    out_path = tmp_path / f"{case.id}.yaml"
    out_path.write_text(text, encoding="utf-8")
    assert out_path.read_text(encoding="utf-8") == re_emitted


# ---------------------------------------------------------------------------
# 11. Generator survives a hostile traces directory
# ---------------------------------------------------------------------------


def test_hostile_traces_directory(tmp_path: Path) -> None:
    traces = tmp_path / ".sdd" / "traces"
    traces.mkdir(parents=True)
    # Mix of valid + invalid:
    (traces / "valid.jsonl").write_text(json.dumps({"tag": "large_diff"}) + "\n", encoding="utf-8")
    (traces / "empty.jsonl").write_text("", encoding="utf-8")
    (traces / "garbage.jsonl").write_text("not json at all", encoding="utf-8")
    (traces / "broken-utf8.jsonl").write_bytes(b"\xff\xfe\x00\x00 trash\n")

    result = generate_from_traces(workdir=tmp_path, from_traces=10, seed=42)
    # Must surface large_diff from the valid file regardless of noise.
    scenarios = {c.scenario for c in result.created}
    assert "large_diff" in scenarios


# ---------------------------------------------------------------------------
# 12. Skipping when no traces dir exists
# ---------------------------------------------------------------------------


def test_missing_traces_dir_falls_back_to_default_corpus(tmp_path: Path) -> None:
    # No .sdd/traces directory at all.
    result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
    assert all(c.scenario == "prompt_injection" for c in result.created)
    assert (tmp_path / Path(*DEFAULT_OUT_DIR)).is_dir()
