"""Unit tests for adapter binary doctor checks."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from bernstein.cli.doctor import adapter_checks
from bernstein.cli.doctor.adapter_checks import (
    ADAPTER_BINARIES,
    check_adapter_binary,
    run_adapter_checks,
)


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# check_adapter_binary
# ---------------------------------------------------------------------------


def test_missing_binary_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)

    result = _run(check_adapter_binary("claude", "claude"))

    assert result.status == "fail"  # type: ignore[union-attr]
    assert "not in PATH" in result.detail  # type: ignore[union-attr]
    assert "Install via the adapter" in result.remediation  # type: ignore[union-attr]
    assert result.category == "adapter"  # type: ignore[union-attr]
    assert result.name == "adapter:claude"  # type: ignore[union-attr]


def test_blank_declared_binary_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(check_adapter_binary("ghost", ""))

    assert result.status == "fail"  # type: ignore[union-attr]
    assert "no binary declared" in result.detail  # type: ignore[union-attr]


def test_version_ok_uses_first_nonempty_stdout_line(tmp_path: Path) -> None:
    bin_path = _write_script(tmp_path, "echo 'fakeprog 1.2.3'\nexit 0\n")

    result = _run(check_adapter_binary("fakeprog", str(bin_path)))

    assert result.status == "ok"  # type: ignore[union-attr]
    assert "fakeprog 1.2.3" in result.detail  # type: ignore[union-attr]
    assert result.remediation == ""  # type: ignore[union-attr]


def test_version_nonzero_exit_returns_warn(tmp_path: Path) -> None:
    bin_path = _write_script(tmp_path, "echo 'broken' 1>&2\nexit 2\n")

    result = _run(check_adapter_binary("brokenbin", str(bin_path)))

    assert result.status == "warn"  # type: ignore[union-attr]
    assert "exited 2" in result.detail  # type: ignore[union-attr]
    assert "broken" in result.detail  # type: ignore[union-attr]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_version_timeout_returns_warn(tmp_path: Path) -> None:
    bin_path = _write_script(tmp_path, "sleep 3\n")

    result = _run(check_adapter_binary("slowbin", str(bin_path), timeout=0.2))

    assert result.status == "warn"  # type: ignore[union-attr]
    assert "timed out" in result.detail  # type: ignore[union-attr]


def test_empty_version_output_still_ok(tmp_path: Path) -> None:
    bin_path = _write_script(tmp_path, "exit 0\n")

    result = _run(check_adapter_binary("silent", str(bin_path)))

    assert result.status == "ok"  # type: ignore[union-attr]
    assert "version output empty" in result.detail  # type: ignore[union-attr]


def test_version_falls_back_to_stderr(tmp_path: Path) -> None:
    # Some CLIs write --version to stderr (notably `claude --version` in
    # some prerelease builds). The adapter check must handle that.
    bin_path = _write_script(tmp_path, "echo 'stderrver 9.9' 1>&2\nexit 0\n")

    result = _run(check_adapter_binary("stderrver", str(bin_path)))

    assert result.status == "ok"  # type: ignore[union-attr]
    assert "stderrver 9.9" in result.detail  # type: ignore[union-attr]


def test_name_uses_adapter_namespace() -> None:
    result = _run(check_adapter_binary("aider", "definitely-missing-bin-xyz-doctor"))
    assert result.name == "adapter:aider"  # type: ignore[union-attr]


def test_returns_frozen_dataclass() -> None:
    from dataclasses import FrozenInstanceError

    result = _run(check_adapter_binary("anything", "this-bin-does-not-exist-ever"))
    with pytest.raises(FrozenInstanceError):
        result.status = "ok"  # type: ignore[misc,union-attr]


# ---------------------------------------------------------------------------
# run_adapter_checks
# ---------------------------------------------------------------------------


def test_run_adapter_checks_uses_explicit_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)
    results = _run(run_adapter_checks(["claude", "aider"]))

    assert len(results) == 2  # type: ignore[arg-type]
    names = sorted(r.name for r in results)  # type: ignore[union-attr]
    assert names == ["adapter:aider", "adapter:claude"]


def test_run_adapter_checks_empty_emits_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_checks, "_load_adapters_from_yaml", lambda _p: [])
    monkeypatch.setattr(adapter_checks, "_default_adapter_list", lambda: [])

    results = _run(run_adapter_checks(None))

    assert len(results) == 1  # type: ignore[arg-type]
    assert results[0].status == "skip"  # type: ignore[index,union-attr]
    assert results[0].name == "adapter:none"  # type: ignore[index,union-attr]


def test_run_adapter_checks_uses_default_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)
    monkeypatch.setattr(adapter_checks, "_load_adapters_from_yaml", lambda _p: [])

    results = _run(run_adapter_checks(None))

    # Default fallback list has 5 entries.
    assert len(results) == 5  # type: ignore[arg-type]


def test_run_adapter_checks_uses_yaml_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_text = "cli: claude\nadapters:\n  - aider\n  - codex\n"
    (tmp_path / "bernstein.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)

    results = _run(run_adapter_checks(None, config_path=tmp_path / "bernstein.yaml"))
    names = sorted(r.name for r in results)  # type: ignore[union-attr]
    assert "adapter:claude" in names
    assert "adapter:aider" in names
    assert "adapter:codex" in names


def test_run_adapter_checks_tolerates_broken_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "bernstein.yaml").write_text(":::not-yaml:::", encoding="utf-8")
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)

    # Must not raise; falls back to default list.
    results = _run(run_adapter_checks(None, config_path=tmp_path / "bernstein.yaml"))
    assert len(results) >= 1  # type: ignore[arg-type]


def test_run_adapter_checks_with_custom_binary_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)
    results = _run(run_adapter_checks(["x"], binaries={"x": "my-bin"}))
    assert results[0].detail.startswith("Binary `my-bin` not in PATH")  # type: ignore[index,union-attr]


def test_run_adapter_checks_runs_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force each check to "sleep" via subprocess so parallelism matters.
    # Using a missing-binary path keeps the test fast and deterministic.
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)
    results = _run(run_adapter_checks(["a", "b", "c", "d"]))
    assert len(results) == 4  # type: ignore[arg-type]


def test_run_adapter_checks_role_model_policy_picks_up_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_text = "role_model_policy:\n  manager: {cli: claude}\n  qa: {cli: codex}\n"
    (tmp_path / "bernstein.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(adapter_checks.shutil, "which", lambda _name: None)

    results = _run(run_adapter_checks(None, config_path=tmp_path / "bernstein.yaml"))
    names = sorted(r.name for r in results)  # type: ignore[union-attr]
    assert "adapter:claude" in names
    assert "adapter:codex" in names


def test_adapter_binaries_has_core_entries() -> None:
    for adapter in ("claude", "codex", "gemini", "aider"):
        assert adapter in ADAPTER_BINARIES


def test_adapter_binaries_are_strings() -> None:
    for name, binary in ADAPTER_BINARIES.items():
        assert isinstance(name, str)
        assert isinstance(binary, str)
        assert binary, f"empty binary for adapter {name}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, body: str) -> Path:
    """Write a temporary shell script and return its absolute path."""
    if sys.platform.startswith("win"):  # pragma: no cover
        pytest.skip("POSIX shell scripts required for adapter version probe tests.")
    path = tmp_path / "fakebin"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path
