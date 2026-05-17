"""End-to-end integration tests for the auto-heal v2 entry-point script.

These exercise ``scripts/auto_heal_v2_run.py`` via ``subprocess`` so we
verify the actual CLI surface that the GitHub Actions YAML invokes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/auto_heal_v2_run.py")


def _run(
    args: list[str],
    *,
    sdd_dir: Path,
    stdin: str = "",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["BERNSTEIN_SDD_DIR"] = str(sdd_dir)
    env["PYTHONPATH"] = str(Path("src").resolve()) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_script_file_exists() -> None:
    assert SCRIPT.exists()


def test_triage_returns_buckets(tmp_path: Path) -> None:
    result = _run(
        ["triage"],
        sdd_dir=tmp_path,
        stdin="Lint\nSpelling (typos)\nType check\n",
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["safe"] == ["Lint"]
    assert out["heuristic"] == ["Spelling (typos)"]
    assert out["risky"] == ["Type check"]
    assert out["should_heal"] is True


def test_triage_empty_input(tmp_path: Path) -> None:
    result = _run(["triage"], sdd_dir=tmp_path, stdin="")
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["safe"] == []
    assert out["should_heal"] is False


def test_kill_switch_default_enabled(tmp_path: Path) -> None:
    # No file present -> enabled.
    result = _run(["check-kill-switch"], sdd_dir=tmp_path)
    assert result.returncode == 0


def test_kill_switch_forever_disables(tmp_path: Path) -> None:
    (tmp_path / "autoheal-disabled").write_text("forever\n", encoding="utf-8")
    result = _run(["check-kill-switch"], sdd_dir=tmp_path)
    assert result.returncode == 1


def test_select_strategy_picks_known(tmp_path: Path) -> None:
    result = _run(
        ["select-strategy", "--candidates", "ruff-format,typos-allowlist"],
        sdd_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in ("ruff-format", "typos-allowlist")


def test_select_strategy_empty_candidates_fails(tmp_path: Path) -> None:
    result = _run(["select-strategy", "--candidates", ""], sdd_dir=tmp_path)
    assert result.returncode != 0


def test_record_outcome_creates_state_files(tmp_path: Path) -> None:
    result = _run(
        [
            "record-outcome",
            "--strategy",
            "ruff-format",
            "--cls",
            "safe",
            "--job",
            "Lint",
            "--outcome",
            "success",
        ],
        sdd_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    bandit_path = tmp_path / "autoheal-bandit.json"
    bayes_path = tmp_path / "autoheal-bayes.json"
    assert bandit_path.exists()
    assert bayes_path.exists()
    bandit = json.loads(bandit_path.read_text(encoding="utf-8"))
    assert "ruff-format" in bandit["arms"]
    assert bandit["arms"]["ruff-format"]["alpha"] == 2.0


def test_record_outcome_failure_updates_beta(tmp_path: Path) -> None:
    _run(
        [
            "record-outcome",
            "--strategy",
            "x",
            "--cls",
            "safe",
            "--job",
            "Lint",
            "--outcome",
            "fail",
        ],
        sdd_dir=tmp_path,
    )
    bandit = json.loads((tmp_path / "autoheal-bandit.json").read_text(encoding="utf-8"))
    assert bandit["arms"]["x"]["beta"] == 2.0


def test_log_appends_to_history(tmp_path: Path) -> None:
    body = {
        "run_id": "run-42",
        "head_sha": "abcdef123",
        "strategy": "ruff-format",
        "cls": "safe",
        "confidence": 0.85,
        "outcome": "applied",
        "cost_usd": 0.0,
        "llm_calls": 0,
        "patch_sha": "dead",
        "decision_id": "dec-x",
        "rationale": "format only",
    }
    result = _run(["log"], sdd_dir=tmp_path, stdin=json.dumps(body))
    assert result.returncode == 0, result.stderr
    hist = tmp_path / "autoheal-history.jsonl"
    assert hist.exists()
    lines = hist.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["run_id"] == "run-42"
    assert parsed["outcome"] == "applied"


def test_log_rejects_bad_json(tmp_path: Path) -> None:
    result = _run(["log"], sdd_dir=tmp_path, stdin="not json")
    assert result.returncode != 0


@pytest.mark.parametrize(
    "stdin_jobs,expected_class",
    [
        ("Lint", "safe"),
        ("Spelling (typos)", "heuristic"),
        ("Type check", "risky"),
        ("Mystery job", "unknown"),
    ],
)
def test_triage_known_jobs(tmp_path: Path, stdin_jobs: str, expected_class: str) -> None:
    result = _run(["triage"], sdd_dir=tmp_path, stdin=stdin_jobs + "\n")
    out = json.loads(result.stdout)
    assert stdin_jobs in out[expected_class]
