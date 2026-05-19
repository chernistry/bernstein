"""Tests for the filesystem-as-service-registry directory loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bernstein.core.fleet.config import DEFAULT_TASK_SERVER_PORT
from bernstein.core.fleet.directory_registry import (
    DISABLED_FLAG_FILENAME,
    MANIFEST_FILENAME,
    DirectoryRegistry,
    InstanceSpec,
    default_fleet_root,
    load_directory_registry,
)


def _write_instance(
    root: Path,
    name: str,
    *,
    manifest: str | None = "",
    disabled: bool = False,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (directory / MANIFEST_FILENAME).write_text(manifest, encoding="utf-8")
    if disabled:
        (directory / DISABLED_FLAG_FILENAME).write_text("", encoding="utf-8")
    return directory


def test_default_fleet_root_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BERNSTEIN_FLEET_ROOT", str(tmp_path / "custom"))
    assert default_fleet_root() == tmp_path / "custom"


def test_default_fleet_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_FLEET_ROOT", raising=False)
    root = default_fleet_root()
    assert root == Path("~/.bernstein/fleet").expanduser()


def test_scan_missing_root_reports_error(tmp_path: Path) -> None:
    registry = DirectoryRegistry(tmp_path / "nope")
    result = registry.scan()
    assert result.instances == []
    assert any("does not exist" in err.message for err in result.errors)


def test_scan_root_must_be_directory(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("", encoding="utf-8")
    registry = DirectoryRegistry(target)
    result = registry.scan()
    assert result.instances == []
    assert any("not a directory" in err.message for err in result.errors)


def test_scan_returns_empty_for_empty_root(tmp_path: Path) -> None:
    registry = DirectoryRegistry(tmp_path)
    result = registry.scan()
    assert result.instances == []
    assert result.errors == []
    assert result.disabled == []
    assert result.root == tmp_path


def test_scan_picks_up_minimal_instance(tmp_path: Path) -> None:
    _write_instance(tmp_path, "alpha", manifest="")
    result = DirectoryRegistry(tmp_path).scan()
    assert len(result.instances) == 1
    spec = result.instances[0]
    assert spec.name == "alpha"
    assert spec.directory == tmp_path / "alpha"
    assert spec.project_path == tmp_path / "alpha"
    assert spec.task_server_url == f"http://127.0.0.1:{DEFAULT_TASK_SERVER_PORT}"
    assert spec.disabled is False


def test_scan_honours_manifest_overrides(tmp_path: Path) -> None:
    project = tmp_path / "elsewhere"
    project.mkdir()
    manifest = f"""
name: beta
path: {project}
task_server_url: http://127.0.0.1:9099
"""
    _write_instance(tmp_path, "raw-dir-name", manifest=manifest)
    result = DirectoryRegistry(tmp_path).scan()
    assert len(result.instances) == 1
    spec = result.instances[0]
    assert spec.name == "beta"
    assert spec.project_path == project.resolve()
    assert spec.task_server_url == "http://127.0.0.1:9099"


def test_scan_resolves_relative_path_against_directory(tmp_path: Path) -> None:
    _write_instance(tmp_path, "gamma", manifest="path: nested-root\n")
    nested = tmp_path / "gamma" / "nested-root"
    nested.mkdir()
    result = DirectoryRegistry(tmp_path).scan()
    assert len(result.instances) == 1
    assert result.instances[0].project_path == nested.resolve()


def test_scan_skips_disabled_instance(tmp_path: Path) -> None:
    _write_instance(tmp_path, "active", manifest="")
    _write_instance(tmp_path, "asleep", manifest="", disabled=True)
    result = DirectoryRegistry(tmp_path).scan()
    assert [s.name for s in result.instances] == ["active"]
    assert [s.name for s in result.disabled] == ["asleep"]


def test_scan_ignores_subdirs_without_manifest(tmp_path: Path) -> None:
    _write_instance(tmp_path, "real", manifest="")
    (tmp_path / "stray").mkdir()
    result = DirectoryRegistry(tmp_path).scan()
    assert [s.name for s in result.instances] == ["real"]
    assert result.errors == []


def test_scan_ignores_hidden_dirs(tmp_path: Path) -> None:
    _write_instance(tmp_path, ".hidden", manifest="")
    _write_instance(tmp_path, "shown", manifest="")
    result = DirectoryRegistry(tmp_path).scan()
    assert [s.name for s in result.instances] == ["shown"]


def test_scan_reports_yaml_parse_error(tmp_path: Path) -> None:
    _write_instance(tmp_path, "broken", manifest="this: is: invalid: yaml: ::\n")
    result = DirectoryRegistry(tmp_path).scan()
    assert result.instances == []
    assert any("YAML parse error" in err.message for err in result.errors)


def test_scan_rejects_non_mapping_manifest(tmp_path: Path) -> None:
    _write_instance(tmp_path, "listy", manifest="- one\n- two\n")
    result = DirectoryRegistry(tmp_path).scan()
    assert result.instances == []
    assert any("YAML mapping" in err.message for err in result.errors)


def test_scan_rejects_empty_name(tmp_path: Path) -> None:
    _write_instance(tmp_path, "empty-name", manifest="name: ''\n")
    result = DirectoryRegistry(tmp_path).scan()
    assert result.instances == []
    assert any("non-empty string" in err.message for err in result.errors)


def test_scan_rejects_non_string_url(tmp_path: Path) -> None:
    _write_instance(tmp_path, "badurl", manifest="task_server_url: 1234\n")
    result = DirectoryRegistry(tmp_path).scan()
    assert result.instances == []
    assert any("task_server_url" in err.message for err in result.errors)


def test_scan_rejects_duplicate_names(tmp_path: Path) -> None:
    _write_instance(tmp_path, "one", manifest="name: shared\n")
    _write_instance(tmp_path, "two", manifest="name: shared\n")
    result = DirectoryRegistry(tmp_path).scan()
    assert len(result.instances) == 1
    assert result.instances[0].directory.name == "one"
    assert any("duplicate" in err.message for err in result.errors)


def test_load_directory_registry_convenience(tmp_path: Path) -> None:
    _write_instance(tmp_path, "delta", manifest="")
    result = load_directory_registry(tmp_path)
    assert [s.name for s in result.instances] == ["delta"]


def test_to_project_config_round_trip(tmp_path: Path) -> None:
    spec = InstanceSpec(
        name="zeta",
        directory=tmp_path,
        project_path=tmp_path,
        task_server_url="http://127.0.0.1:8052",
    )
    project = spec.to_project_config()
    assert project.name == "zeta"
    assert project.path == tmp_path
    assert project.sdd_dir == tmp_path / ".sdd"
    assert project.task_server_url == "http://127.0.0.1:8052"


def test_as_fleet_config_projects_disabled_as_errors(tmp_path: Path) -> None:
    _write_instance(tmp_path, "active", manifest="")
    _write_instance(tmp_path, "asleep", manifest="", disabled=True)
    config = DirectoryRegistry(tmp_path).as_fleet_config()
    assert [p.name for p in config.projects] == ["active"]
    assert any("asleep" in err.message and ".disabled" in err.message for err in config.errors)
    assert config.source_path == tmp_path


def test_registry_uses_env_root_when_root_not_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BERNSTEIN_FLEET_ROOT", str(tmp_path))
    _write_instance(tmp_path, "eta", manifest="")
    registry = DirectoryRegistry()
    assert registry.root == tmp_path
    result = registry.scan()
    assert [s.name for s in result.instances] == ["eta"]


def test_unreadable_manifest_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":  # pragma: no cover - permission semantics differ on Windows
        pytest.skip("chmod-based permission tests are POSIX-only")
    _write_instance(tmp_path, "locked", manifest="")
    manifest_path = tmp_path / "locked" / MANIFEST_FILENAME
    manifest_path.chmod(0o000)
    try:
        if os.access(manifest_path, os.R_OK):
            pytest.skip("filesystem ignored chmod (likely running as root)")
        result = DirectoryRegistry(tmp_path).scan()
        assert result.instances == []
        assert any("cannot read" in err.message for err in result.errors)
    finally:
        manifest_path.chmod(0o644)
