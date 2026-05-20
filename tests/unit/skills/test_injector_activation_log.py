"""Activation log integration via ``inject_skills`` (#1720, Track 5 floor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bernstein.core.models import Task

from bernstein.adapters.skills_injector import inject_skills
from bernstein.core.skills.activation_log import ENV_VAR, activation_log_path


def _make_skill_templates(root: Path) -> Path:
    """Mirror the minimal template tree used by the existing injector tests."""
    skills_dir = root / "templates" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bernstein-completion-protocol.md").write_text(
        "---\nname: bernstein-completion-protocol\n"
        "description: Report task completion to the orchestrator after every task.\n"
        "version: 1.2.3\n---\n\n# Completion\nComplete: {{COMPLETE_CMDS}}\n",
        encoding="utf-8",
    )
    (skills_dir / "bernstein-signal-check.md").write_text(
        "---\nname: bernstein-signal-check\n"
        "description: Poll the runtime signal directory for wake-up requests.\n"
        "version: 2.0.0\n---\n\n# Signal\nSignals at {{SESSION_ID}}\n",
        encoding="utf-8",
    )
    return root / "templates" / "roles"


def _make_task(task_id: str = "T-001") -> Task:
    return Task(id=task_id, title="Test task", description="A test task", role="backend")


def test_inject_skills_writes_activation_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each injected skill produces one activation log line per task."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    templates_dir = _make_skill_templates(tmp_path)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="security",  # role with no extra skills -> only always-inject pair
        tasks=[_make_task("T-001")],
        session_id="sec-abc",
        templates_dir=templates_dir,
    )

    log_path = activation_log_path(workdir)
    assert log_path.is_file()
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    # Two always-inject skills, one task = two activation records. The
    # exact count guard catches duplicate-log regressions that a
    # set-equality check would silently swallow.
    assert len(lines) == 2
    skills_seen = {row["skill"] for row in lines}
    assert skills_seen == {"bernstein-completion-protocol", "bernstein-signal-check"}
    for row in lines:
        assert row["role"] == "security"
        assert row["task_id"] == "T-001"
        assert row["trigger_source"] == "role-binding"
        assert row["digest"]  # non-empty
        assert row["timestamp"].endswith("Z")


def test_inject_skills_respects_activation_log_env_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ``BERNSTEIN_SKILL_ACTIVATION_LOG=0`` suppresses log writes."""
    monkeypatch.setenv(ENV_VAR, "0")
    templates_dir = _make_skill_templates(tmp_path)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="security",
        tasks=[_make_task("T-002")],
        session_id="sec-def",
        templates_dir=templates_dir,
    )

    # Skills are still injected -> .claude/skills/ exists with the files.
    assert (workdir / ".claude" / "skills" / "bernstein-completion-protocol.md").is_file()
    # But the activation log is not created.
    assert not activation_log_path(workdir).exists()
