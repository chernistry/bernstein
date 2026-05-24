"""End-to-end integration tests for ``bernstein simulate`` (issue #1374).

These tests exercise the full pipeline against real seed plans shipped
in ``examples/plans/*.yaml`` plus synthesized large fixtures. They run
without spawning a real agent or hitting the network - the simulator
stays in-process.

Coverage:

* Each seed plan produces a non-empty report.
* The simulator survives a generated 50-task plan.
* JSON + Markdown sidecars on disk are syntactically valid.
* The CLI invoked end-to-end matches the in-process API.
* Trace-history calibration changes the abandon prediction.
* Predicted band envelopes a tiny fake run (within tolerance).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from bernstein.cli.commands.simulate_cmd import simulate_cmd
from bernstein.core.simulate import SimulationOptions, render_markdown, simulate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "examples" / "plans"


def _seed_plans() -> list[Path]:
    if not _EXAMPLES.exists():
        return []
    return sorted(_EXAMPLES.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Seed plans render a non-empty report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan_path", _seed_plans())
def test_each_seed_plan_simulates(plan_path: Path) -> None:
    report = simulate(plan_path, SimulationOptions(seed=1))
    assert report.task_count > 0
    # Cost band is always non-negative.
    assert report.aggregate.total_cost_p50 >= 0.0
    assert report.aggregate.total_cost_p90 >= report.aggregate.total_cost_p50
    # Wall-clock band ordered.
    assert report.aggregate.wall_clock_p90 >= report.aggregate.wall_clock_p50


@pytest.mark.parametrize("plan_path", _seed_plans())
def test_each_seed_plan_renders_markdown(plan_path: Path) -> None:
    report = simulate(plan_path, SimulationOptions(seed=1))
    md = render_markdown(report)
    assert "# Bernstein simulate" in md
    assert "## TL;DR" in md


# ---------------------------------------------------------------------------
# Large synthesized plan
# ---------------------------------------------------------------------------


def _large_plan(tmp_path: Path, *, task_count: int = 50) -> Path:
    stages: list[dict[str, Any]] = []
    # 5 sequential stages of N/5 tasks each.
    per_stage = max(1, task_count // 5)
    for stage_idx in range(5):
        steps = [{"title": f"task-{stage_idx}-{i}", "role": "backend"} for i in range(per_stage)]
        stage: dict[str, Any] = {"name": f"Stage{stage_idx}", "steps": steps}
        if stage_idx > 0:
            stage["depends_on"] = [f"Stage{stage_idx - 1}"]
        stages.append(stage)
    plan = {"name": "Large", "stages": stages}
    target = tmp_path / "large.yaml"
    target.write_text(yaml.safe_dump(plan), encoding="utf-8")
    return target


def test_simulate_large_plan_runs(tmp_path: Path) -> None:
    plan = _large_plan(tmp_path, task_count=50)
    report = simulate(plan, SimulationOptions(seed=1))
    assert report.task_count >= 45


def test_simulate_large_plan_critical_path_smaller_than_sum(tmp_path: Path) -> None:
    plan = _large_plan(tmp_path, task_count=50)
    report = simulate(plan, SimulationOptions(seed=1))
    sum_p50 = sum(t.latency_p50 for t in report.tasks)
    # With parallel tasks per stage, the critical path must be shorter
    # than the sum of all per-task latencies.
    assert report.aggregate.wall_clock_p50 < sum_p50


# ---------------------------------------------------------------------------
# CLI end-to-end on a seed plan
# ---------------------------------------------------------------------------


def test_cli_end_to_end_on_seed_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plans = _seed_plans()
    if not plans:
        pytest.skip("no seed plans found")
    plan = plans[0]
    out_path = tmp_path / "report.json"
    # Keep repo-local .sdd calibration dirs from changing the CLI defaults under test.
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        simulate_cmd,
        [str(plan), "--format", "json", "--out", str(out_path), "--seed", "3"],
    )
    assert result.exit_code == 0, result.output
    sidecar = json.loads(out_path.read_text(encoding="utf-8"))
    # Sanity: sidecar mirrors the in-process simulate() result.
    via_api = simulate(plan, SimulationOptions(seed=3))
    assert sidecar == via_api.to_dict()


def test_cli_markdown_sidecar_matches_render(tmp_path: Path) -> None:
    plans = _seed_plans()
    if not plans:
        pytest.skip("no seed plans found")
    plan = plans[0]
    out_path = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(
        simulate_cmd,
        [str(plan), "--out", str(out_path), "--seed", "1"],
    )
    assert result.exit_code == 0, result.output
    body = out_path.read_text(encoding="utf-8")
    assert "## Per-task predictions" in body


# ---------------------------------------------------------------------------
# Trace calibration changes prediction
# ---------------------------------------------------------------------------


def test_trace_calibration_changes_abandon_prediction(tmp_path: Path) -> None:
    plan_dict: dict[str, Any] = {
        "name": "Calibration",
        "stages": [{"name": "Only", "steps": [{"title": "Single task", "role": "backend"}]}],
    }
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan_dict), encoding="utf-8")

    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    # 8 of 10 abandoned (80%).
    lines: list[str] = []
    for i in range(10):
        status = "abandoned" if i < 8 else "completed"
        lines.append('{"role": "backend", "adapter": "mock", "status": "' + status + '"}')
    (traces_dir / "t.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    cold = simulate(plan_path, SimulationOptions(seed=1))
    hot = simulate(plan_path, SimulationOptions(seed=1, traces_dir=str(traces_dir)))
    assert cold.tasks[0].abandon_probability < hot.tasks[0].abandon_probability
    assert hot.tasks[0].abandon_probability == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Fake-cli comparison: predicted vs actual on a 1-task run
# ---------------------------------------------------------------------------


def test_predicted_cost_band_envelopes_a_single_known_sample(tmp_path: Path) -> None:
    """When a single historical sample of $0.50 exists, the band should bracket it."""
    plan_dict: dict[str, Any] = {
        "name": "Compare",
        "stages": [{"name": "Only", "steps": [{"title": "Task", "role": "backend"}]}],
    }
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan_dict), encoding="utf-8")

    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    body_lines = ['{"role": "backend", "adapter": "mock", "cost_usd": 0.50}' for _ in range(20)]
    (metrics_dir / "cost.jsonl").write_text("\n".join(body_lines) + "\n", encoding="utf-8")

    report = simulate(plan_path, SimulationOptions(seed=1, metrics_dir=str(metrics_dir)))
    task = report.tasks[0]
    # Tolerance allows for the deterministic jitter the runner applies.
    assert task.cost_p50 == pytest.approx(0.5, rel=0.1)
    assert task.cost_p90 == pytest.approx(0.5, rel=0.1)


def test_cold_start_report_flags_cold_start(tmp_path: Path) -> None:
    plan_dict: dict[str, Any] = {
        "name": "Cold",
        "stages": [{"name": "Only", "steps": [{"title": "Task", "role": "backend"}]}],
    }
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan_dict), encoding="utf-8")

    report = simulate(plan_path, SimulationOptions(seed=1))
    assert report.cold_start is True
    assert any("cold-start" in n or "heuristic" in n for n in report.notes)
