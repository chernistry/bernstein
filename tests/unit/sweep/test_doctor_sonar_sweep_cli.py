"""Smoke test for ``bernstein doctor sonar-sweep`` CLI wiring."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.advanced_cmd import doctor

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issues_search.json"


def test_doctor_sonar_sweep_help() -> None:
    runner = CliRunner()
    result = runner.invoke(doctor, ["sonar-sweep", "--help"])
    assert result.exit_code == 0, result.output
    assert "static-analysis" in result.output.lower()
    assert "--dry-run" in result.output
    assert "--severity-min" in result.output
    assert "--max-per-day" in result.output


def test_doctor_sonar_sweep_dry_run_with_fixture(tmp_path: Path) -> None:
    runner = CliRunner()
    out_dir = tmp_path / "open"
    result = runner.invoke(
        doctor,
        [
            "sonar-sweep",
            "--dry-run",
            "--severity-min",
            "BLOCKER",
            "--max-per-day",
            "5",
            "--out-dir",
            str(out_dir),
            "--fixture",
            str(_FIXTURE),
        ],
    )
    assert result.exit_code == 0, result.output
    # Dry-run never writes anything.
    if out_dir.exists():
        assert list(out_dir.glob("*.md")) == []
    assert "would-emit" in result.output
