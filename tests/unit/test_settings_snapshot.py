"""Tests for settings_snapshot — trace settings capture and serialization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bernstein.settings_snapshot import (
    SettingsSnapshot,
    SettingValue,
    _coerce_type,
    capture_settings,
)

# --- Fixtures ---


@pytest.fixture()
def project_with_config(tmp_path: Path) -> Path:
    """Create a project dir with bernstein.yaml settings."""
    (tmp_path / "bernstein.yaml").write_text(
        "model: sonnet\n"
        "effort: high\n"
        "parallelism: 3\n"
        "approval_mode: review\n"
    )
    return tmp_path


@pytest.fixture()
def project_with_sdd_config(tmp_path: Path) -> Path:
    """Create a project dir with .sdd/config.yaml settings."""
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    (sdd / "config.yaml").write_text(
        "model: opus\n"
        "effort: max\n"
        "max_tokens: 50000\n"
    )
    return tmp_path


# --- TestSettingValue ---


class TestSettingValue:
    def test_to_dict(self) -> None:
        sv = SettingValue(name="model", value="sonnet", source="config")
        d = sv.to_dict()
        assert d["name"] == "model"
        assert d["value"] == "sonnet"
        assert d["source"] == "config"


# --- TestSettingsSnapshot ---


class TestSettingsSnapshot:
    def test_to_dict(self) -> None:
        snap = SettingsSnapshot(
            capture_ts=1000.0,
            workdir="/test",
            settings=[SettingValue(name="model", value="opus", source="env")],
        )
        d = snap.to_dict()
        assert d["capture_ts"] == 1000.0
        assert d["workdir"] == "/test"
        assert len(d["settings"]) == 1

    def test_save_writes_file(self, tmp_path: Path) -> None:
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        snap = SettingsSnapshot(capture_ts=1000.0, workdir="/p")
        snap.settings.append(SettingValue(name="model", value="sonnet", source="default"))
        path = snap.save(traces_dir)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["capture_ts"] == 1000.0
        assert len(data["settings"]) == 1


# --- TestCoerceType ---


class TestCoerceType:
    def test_bool_true(self) -> None:
        assert _coerce_type("true") is True
        assert _coerce_type("True") is True
        assert _coerce_type("1") is True
        assert _coerce_type("yes") is True

    def test_bool_false(self) -> None:
        assert _coerce_type("false") is False
        assert _coerce_type("False") is False
        assert _coerce_type("0") is False
        assert _coerce_type("no") is False

    def test_int(self) -> None:
        assert _coerce_type("42") == 42
        assert _coerce_type("-3") == -3

    def test_float(self) -> None:
        assert _coerce_type("3.14") == 3.14

    def test_string_passthrough(self) -> None:
        assert _coerce_type("sonnet") == "sonnet"
        assert _coerce_type("sonnet") != 42


# --- TestCaptureSettings ---


class TestCaptureSettings:
    def test_reads_from_env(self, tmp_path: Path) -> None:
        env = {
            "BERNSTEIN_MODEL": "opus",
            "BERNSTEIN_EFFORT": "max",
        }
        with patch.dict("os.environ", env, clear=False):
            snap = capture_settings(tmp_path)
        model_sv = next(s for s in snap.settings if s.name == "model")
        assert model_sv.value is True or model_sv.value == "opus"
        assert model_sv.source == "env"

    def test_reads_from_config(self, project_with_config: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            snap = capture_settings(project_with_config)
        model_sv = next(s for s in snap.settings if s.name == "model")
        assert model_sv.value == "sonnet"
        assert model_sv.source == "config"

    def test_env_overrides_config(self, project_with_config: Path) -> None:
        with patch.dict("os.environ", {"BERNSTEIN_MODEL": "opus"}):
            snap = capture_settings(project_with_config)
        model_sv = next(s for s in snap.settings if s.name == "model")
        assert model_sv.source == "env"
        assert model_sv.raw_value == "opus"

    def test_sdd_config_overrides_root(self, project_with_config: Path) -> None:
        # Create .sdd/config.yaml that overrides bernstein.yaml
        sdd_cfg = project_with_config / ".sdd" / "config.yaml"
        sdd_cfg.parent.mkdir(parents=True, exist_ok=True)
        sdd_cfg.write_text("model: haiku\n")
        with patch.dict("os.environ", {}, clear=True):
            snap = capture_settings(project_with_config)
        model_sv = next(s for s in snap.settings if s.name == "model")
        assert model_sv.value == "haiku"
        assert model_sv.source == "config"

    def test_defaults_when_not_set(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            snap = capture_settings(tmp_path)
        defaults = [s for s in snap.settings if s.source == "default"]
        assert len(defaults) > 0
        for d in defaults:
            assert d.value is None

    def test_timestamp_is_set(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            snap = capture_settings(tmp_path)
        assert snap.capture_ts > 0
