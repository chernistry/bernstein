"""Tests for ``bernstein init`` and the dry-run plan renderer.

``init`` is the workspace-bootstrap command. It is filesystem-only (no network,
no agents), so it runs end-to-end inside an isolated cwd:

  * fresh init creates .sdd structure + config.yaml + bernstein.yaml + .gitignore
  * project-type detection drives the generated bernstein.yaml constraints
  * idempotency: a second run preserves an operator-edited bernstein.yaml
  * --add-badge with no README is skipped (not fatal)
  * --dir initialises a sub-directory
  * help surface

The dry-run renderer ``_show_dry_run_plan`` is exercised directly with a real
plan file (captured via the Rich console recorder), covering the scheduling
table + cost estimate + empty-plan branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.run_bootstrap import _show_dry_run_plan, console, init

_PLAN_YAML = """
name: Test Plan
stages:
  - name: build
    steps:
      - title: Implement parser
        role: backend
      - title: Write tests
        role: qa
"""


@pytest.fixture(autouse=True)
def _quiet_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the init banner quiet and deterministic across environments."""
    monkeypatch.setenv("BERNSTEIN_QUIET", "1")
    # Never let init auto-enable remote mode from a stray Codespaces var.
    monkeypatch.delenv("CODESPACES", raising=False)
    monkeypatch.delenv("BERNSTEIN_REMOTE_QUICKSTART", raising=False)


# ---------------------------------------------------------------------------
# init - fresh
# ---------------------------------------------------------------------------


def test_init_creates_workspace_scaffold() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init, ["--dir", "."])
        assert result.exit_code == 0, result.output
        assert "Done." in result.output
        assert Path(".sdd/config.yaml").exists()
        assert Path("bernstein.yaml").exists()
        assert Path(".sdd/runtime/.gitignore").exists()
        # The runtime dir is gitignored at the repo root.
        assert ".sdd/runtime/" in Path(".gitignore").read_text()
        # config.yaml carries the documented defaults.
        cfg = Path(".sdd/config.yaml").read_text()
        assert "server_port: 8052" in cfg
        assert "default_model: sonnet" in cfg


def test_init_python_project_constraints() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("pyproject.toml").write_text('[project]\nname = "x"\n')
        result = runner.invoke(init, ["--dir", "."])
        assert result.exit_code == 0, result.output
        assert "python" in result.output.lower()
        # The generated config inherits python-flavoured constraints.
        assert "Python 3.12+" in Path("bernstein.yaml").read_text()


def test_init_is_idempotent_for_user_config() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(init, ["--dir", "."])
        # Operator edits bernstein.yaml.
        Path("bernstein.yaml").write_text("# custom\ngoal: keep me\n")
        second = runner.invoke(init, ["--dir", "."])
        assert second.exit_code == 0, second.output
        # The second init must not clobber the edited file.
        assert "keep me" in Path("bernstein.yaml").read_text()


def test_init_add_badge_without_readme_is_skipped() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init, ["--dir", ".", "--add-badge"])
        assert result.exit_code == 0, result.output
        assert "Skipped" in result.output
        # No README was created just to hold a badge.
        assert not Path("README.md").exists()


def test_init_into_subdirectory() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("proj").mkdir()
        result = runner.invoke(init, ["--dir", "proj"])
        assert result.exit_code == 0, result.output
        assert Path("proj/bernstein.yaml").exists()
        assert Path("proj/.sdd/config.yaml").exists()


def test_init_invalid_badge_variant_is_usage_error() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init, ["--dir", ".", "--add-badge", "--badge-variant", "bogus"])
    assert result.exit_code == 2, result.output


def test_init_help_lists_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(init, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--add-badge" in result.output
    assert "--remote" in result.output


# ---------------------------------------------------------------------------
# _show_dry_run_plan
# ---------------------------------------------------------------------------


def test_show_dry_run_plan_renders_table_and_cost(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(_PLAN_YAML)

    with console.capture() as cap:
        _show_dry_run_plan(
            workdir=tmp_path,
            plan_file=plan_file,
            goal=None,
            seed_file=None,
            model_override="sonnet",
            cli=None,
        )
    out = cap.get()
    assert "Dry-run mode" in out
    assert "Implement" in out
    assert "Total tasks: 2" in out
    assert "Estimated cost" in out
    assert "Dry run complete" in out


def test_show_dry_run_plan_empty_plan_reports_no_tasks(tmp_path: Path) -> None:
    """A plan whose stages have no steps yields the 'no tasks' branch."""
    plan_file = tmp_path / "empty.yaml"
    plan_file.write_text("name: Empty\nstages:\n  - name: build\n    steps: []\n")

    with console.capture() as cap:
        _show_dry_run_plan(
            workdir=tmp_path,
            plan_file=plan_file,
            goal=None,
            seed_file=None,
            model_override=None,
            cli=None,
        )
    out = cap.get()
    assert "No tasks to schedule" in out
