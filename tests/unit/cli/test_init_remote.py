"""Tests for the ``bernstein init --remote`` flow and Codespaces detection.

The remote-quickstart path exists because the four documented local
install paths (pipx, uv, brew, docker) all assume a local terminal.
GitHub Codespaces gives readers without one a usable entry point, and
the CLI needs to recognise the environment so it does not trip over
local-binary probes that are not expected to succeed in a fresh
container.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.main import cli
from bernstein.cli.run_bootstrap import is_codespace_runtime


@pytest.fixture
def clean_remote_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var the remote-quickstart path inspects."""
    monkeypatch.delenv("CODESPACES", raising=False)
    monkeypatch.delenv("BERNSTEIN_REMOTE_QUICKSTART", raising=False)


def test_is_codespace_runtime_false_when_env_clean(clean_remote_env: None) -> None:
    """No env signal -> not a codespace."""
    assert is_codespace_runtime() is False


def test_is_codespace_runtime_true_on_codespaces_env(
    clean_remote_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CODESPACES=true`` (GitHub-supplied) -> codespace."""
    monkeypatch.setenv("CODESPACES", "true")
    assert is_codespace_runtime() is True


def test_is_codespace_runtime_true_on_quickstart_opt_in(
    clean_remote_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The devcontainer sets ``BERNSTEIN_REMOTE_QUICKSTART=1``."""
    monkeypatch.setenv("BERNSTEIN_REMOTE_QUICKSTART", "1")
    assert is_codespace_runtime() is True


def test_is_codespace_runtime_case_insensitive(
    clean_remote_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub historically sets ``CODESPACES=True`` (mixed case)."""
    monkeypatch.setenv("CODESPACES", "True")
    assert is_codespace_runtime() is True


def test_init_remote_flag_dispatches(
    clean_remote_env: None,
    tmp_path: Path,
) -> None:
    """``bernstein init --remote`` runs end-to-end and prints the marker."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dir", str(tmp_path), "--remote"])
    assert result.exit_code == 0, result.output
    assert "Remote-quickstart mode" in result.output
    # Skipped local-binary probe -> no project-type detection line.
    assert "Detected" not in result.output
    # The workspace skeleton still lands.
    assert (tmp_path / ".sdd").is_dir()
    assert (tmp_path / "bernstein.yaml").is_file()


def test_init_auto_detects_codespace_without_flag(
    clean_remote_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Codespaces shell auto-enables the remote path without the flag."""
    monkeypatch.setenv("CODESPACES", "true")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Remote-quickstart mode" in result.output


def test_init_local_path_unchanged(
    clean_remote_env: None,
    tmp_path: Path,
) -> None:
    """Without ``--remote`` and outside a codespace, the local path runs."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Remote-quickstart mode" not in result.output


def test_doctor_json_runtime_local(
    clean_remote_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bernstein doctor --json`` reports ``runtime: local`` off-codespace."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--json"])
    # The doctor exits 1 if any check fails (likely on a CI host without
    # adapters), but the JSON payload must still parse and carry the
    # runtime field.
    payload = _extract_json_payload(result.output)
    assert payload["runtime"] == "local"
    assert "checks" in payload


def test_doctor_json_runtime_codespace(
    clean_remote_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bernstein doctor --json`` reports ``runtime: codespace`` inside one."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODESPACES", "true")
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--json"])
    payload = _extract_json_payload(result.output)
    assert payload["runtime"] == "codespace"


def test_doctor_json_runtime_quickstart_opt_in(
    clean_remote_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The devcontainer's ``BERNSTEIN_REMOTE_QUICKSTART`` opt-in is honoured."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BERNSTEIN_REMOTE_QUICKSTART", "1")
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--json"])
    payload = _extract_json_payload(result.output)
    assert payload["runtime"] == "codespace"


def _extract_json_payload(output: str) -> dict[str, object]:
    """Pull the JSON object out of mixed stdout (banner + JSON)."""
    start = output.find("{")
    assert start != -1, f"no JSON object in output:\n{output}"
    # The doctor emits one top-level JSON object; find the matching brace.
    depth = 0
    for idx in range(start, len(output)):
        ch = output[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                payload = json.loads(output[start : idx + 1])
                assert isinstance(payload, dict)
                return payload
    raise AssertionError(f"unterminated JSON in output:\n{output}")
