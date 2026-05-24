"""CLI tests for deterministic skill authoring tools (#1720)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from bernstein.cli.commands.skills_cmd import skills_group


def _write_skill(
    root: Path,
    name: str,
    *,
    keywords: tuple[str, ...],
    body: str = "# Skill\n\nUse this skill for deterministic tests.\n",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    frontmatter = yaml.safe_dump(
        {
            "manifest_schema": 1,
            "name": name,
            "description": f"Skill {name} used by deterministic authoring command tests.",
            "trigger_keywords": list(keywords),
            "references": [],
            "scripts": [],
            "assets": [],
        },
        sort_keys=False,
    )
    skill_dir.joinpath("SKILL.md").write_text(
        "---\n" + frontmatter + "---\n" + body,
        encoding="utf-8",
    )
    return skill_dir


def test_skills_test_runs_trigger_suite_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest", "regression"))
    _write_skill(skills_root, "docs-helper", keywords=("markdown", "docs"))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: pytest task
                query: Fix the pytest regression in the task router.
                expect:
                  include:
                    - pytest-helper
                  exclude:
                    - docs-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["test", str(suite)])

    assert result.exit_code == 0
    assert "1 passed" in result.output


def test_skills_test_fails_when_expected_skill_is_not_matched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest",))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: docs task
                query: Update the README.
                expect:
                  include:
                    - pytest-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["test", str(suite)])

    assert result.exit_code == 1
    assert "docs task" in result.output
    assert "missing: pytest-helper" in result.output


def test_skills_diff_reports_structural_changes(tmp_path: Path) -> None:
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left = _write_skill(left_root, "pytest-helper", keywords=("pytest",))
    right = _write_skill(
        right_root,
        "pytest-helper",
        keywords=("pytest", "regression"),
        body="# Skill\n\nUse this skill for regression tests.\n",
    )
    runner = CliRunner()

    result = runner.invoke(skills_group, ["diff", str(left), str(right)])

    assert result.exit_code == 1
    assert "changed" in result.output
    assert "manifest" in result.output
    assert "body" in result.output


def test_skills_bench_requires_explicit_suite_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest",))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: pytest task
                query: Fix the pytest regression.
                expect:
                  include:
                    - pytest-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    missing_suite = runner.invoke(skills_group, ["bench"])
    explicit_suite = runner.invoke(skills_group, ["bench", str(suite), "--iterations", "2"])

    assert missing_suite.exit_code != 0
    assert explicit_suite.exit_code == 0
    assert "2 iteration(s)" in explicit_suite.output


def test_skills_helpfulness_writes_local_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    activations_dir = workdir / ".sdd" / "skills"
    activations_dir.mkdir(parents=True)
    activations_dir.joinpath("activations.jsonl").write_text(
        json.dumps(
            {
                "skill": "pytest-helper",
                "version": "",
                "digest": "",
                "role": "backend",
                "task_id": "task-1",
                "trigger_source": "role-binding",
                "timestamp": "2026-05-22T12:00:00.000Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    metrics_dir = workdir / ".sdd" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.joinpath("task_completion_time_2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "metric_type": "task_completion_time",
                "timestamp": 1.0,
                "value": 3.0,
                "labels": {"task_id": "task-1", "role": "backend", "model": "sonnet", "success": "True"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["helpfulness"])

    assert result.exit_code == 0
    assert "wrote" in result.output
    assert "pytest-helper" in result.output
    assert (workdir / ".sdd" / "skills" / "helpfulness.json").is_file()


def test_skills_bisect_outputs_local_replay_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    activations_dir = workdir / ".sdd" / "skills"
    activations_dir.mkdir(parents=True)
    activations_dir.joinpath("activations.jsonl").write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in (
                {
                    "skill": "pytest-helper",
                    "version": "1.0.0",
                    "digest": "aaa",
                    "role": "backend",
                    "task_id": "task-1",
                    "trigger_source": "role-binding",
                    "timestamp": "2026-05-22T12:00:00.000Z",
                },
                {
                    "skill": "docs-helper",
                    "version": "1.0.0",
                    "digest": "bbb",
                    "role": "docs",
                    "task_id": "task-1",
                    "trigger_source": "auto-route",
                    "timestamp": "2026-05-22T12:01:00.000Z",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    metrics_dir = workdir / ".sdd" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.joinpath("task_completion_time_2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "metric_type": "task_completion_time",
                "timestamp": 1.0,
                "value": 3.0,
                "labels": {"task_id": "task-1", "role": "backend", "model": "sonnet", "success": "False"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["bisect", "task-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task_id"] == "task-1"
    assert payload["outcome"] == "failed"
    assert payload["candidate_count"] == 2
    assert payload["next_probe"]["disable"] == ["pytest-helper"]
    assert [candidate["skill"] for candidate in payload["candidates"]] == ["pytest-helper", "docs-helper"]


def test_skills_bisect_uses_latest_task_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    activations_dir = workdir / ".sdd" / "skills"
    activations_dir.mkdir(parents=True)
    activations_dir.joinpath("activations.jsonl").write_text(
        json.dumps(
            {
                "skill": "pytest-helper",
                "version": "1.0.0",
                "digest": "aaa",
                "role": "backend",
                "task_id": "task-2",
                "trigger_source": "role-binding",
                "timestamp": "2026-05-22T12:00:00.000Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    metrics_dir = workdir / ".sdd" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.joinpath("task_completion_time_2026-05-22.jsonl").write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in (
                {
                    "metric_type": "task_completion_time",
                    "timestamp": 1.0,
                    "value": 3.0,
                    "labels": {"task_id": "task-2", "role": "backend", "model": "sonnet", "success": "False"},
                },
                {
                    "metric_type": "task_completion_time",
                    "timestamp": 2.0,
                    "value": 4.0,
                    "labels": {"task_id": "task-2", "role": "backend", "model": "sonnet", "success": "True"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["bisect", "task-2", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["outcome"] == "passed"


def test_skills_bisect_fails_for_task_without_activations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["bisect", "missing-task"])

    assert result.exit_code == 1
    assert "no activations found" in result.output
