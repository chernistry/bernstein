"""Tests for the activation log (#1720, Track 5 floor)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bernstein.core.skills.activation_log import (
    ENV_VAR,
    ActivationRecord,
    activation_log_path,
    log_activation,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the env-var opt-out does not leak between tests."""
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_log_activation_writes_jsonl_line(tmp_path: Path) -> None:
    record = ActivationRecord(
        skill="bernstein-test-runner",
        role="backend",
        task_id="task-42",
        trigger_source="role-binding",
        version="1.0.0",
        digest="abcd1234",
    )
    log_path = log_activation(
        record,
        workdir=tmp_path,
        now=datetime(2026, 5, 20, 12, 34, 56, 789000, tzinfo=UTC),
    )

    assert log_path == activation_log_path(tmp_path)
    assert log_path is not None
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "skill": "bernstein-test-runner",
        "version": "1.0.0",
        "digest": "abcd1234",
        "role": "backend",
        "task_id": "task-42",
        "trigger_source": "role-binding",
        "timestamp": "2026-05-20T12:34:56.789Z",
    }


def test_log_activation_appends_multiple_records(tmp_path: Path) -> None:
    for idx in range(3):
        log_activation(
            ActivationRecord(skill=f"skill-{idx}", role="qa", task_id=f"task-{idx}"),
            workdir=tmp_path,
        )
    log_path = activation_log_path(tmp_path)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["skill"] for p in parsed] == ["skill-0", "skill-1", "skill-2"]


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off"])
def test_log_activation_opt_out_via_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(ENV_VAR, value)
    result = log_activation(
        ActivationRecord(skill="ignored", role="backend", task_id="task-x"),
        workdir=tmp_path,
    )
    assert result is None
    assert not activation_log_path(tmp_path).exists()


def test_log_activation_default_writes_when_env_unset(
    tmp_path: Path,
) -> None:
    """The opt-in default: logging is on unless explicitly disabled."""
    result = log_activation(
        ActivationRecord(skill="default-on", role="docs", task_id="task-y"),
        workdir=tmp_path,
    )
    assert result is not None
    assert activation_log_path(tmp_path).is_file()


def test_log_activation_truthy_env_does_not_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "1")
    result = log_activation(
        ActivationRecord(skill="enabled", role="qa", task_id="task-z"),
        workdir=tmp_path,
    )
    assert result is not None


def test_activation_log_path_is_under_sdd_skills(tmp_path: Path) -> None:
    expected = tmp_path / ".sdd" / "skills" / "activations.jsonl"
    assert activation_log_path(tmp_path) == expected
