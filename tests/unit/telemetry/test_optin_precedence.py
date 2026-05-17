"""Opt-in precedence resolution tests.

Precedence (highest first):
1. DO_NOT_TRACK=1
2. BERNSTEIN_TELEMETRY env var
3. ~/.bernstein/telemetry.yaml enabled: <bool>
4. Default off
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.telemetry import (
    OptInSource,
    is_enabled,
    resolve,
    write_enabled,
)

# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


def test_default_off_when_nothing_set(tmp_home: Path) -> None:
    state = resolve(env={}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.DEFAULT


def test_is_enabled_default_off(tmp_home: Path) -> None:
    assert is_enabled(env={}, home=tmp_home) is False


def test_default_off_with_unrelated_env(tmp_home: Path) -> None:
    state = resolve(env={"PATH": "/usr/bin"}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.DEFAULT


# ---------------------------------------------------------------------------
# DO_NOT_TRACK
# ---------------------------------------------------------------------------


def test_do_not_track_overrides_everything(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={"DO_NOT_TRACK": "1", "BERNSTEIN_TELEMETRY": "1"}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.DO_NOT_TRACK


def test_do_not_track_zero_is_not_opt_out(tmp_home: Path) -> None:
    """DO_NOT_TRACK only counts when set to literal '1' (W3C convention)."""
    state = resolve(env={"DO_NOT_TRACK": "0"}, home=tmp_home)
    assert state.source is not OptInSource.DO_NOT_TRACK


def test_do_not_track_empty_is_not_opt_out(tmp_home: Path) -> None:
    state = resolve(env={"DO_NOT_TRACK": ""}, home=tmp_home)
    assert state.source is not OptInSource.DO_NOT_TRACK


def test_do_not_track_overrides_bernstein_env(tmp_home: Path) -> None:
    state = resolve(
        env={"DO_NOT_TRACK": "1", "BERNSTEIN_TELEMETRY": "1"},
        home=tmp_home,
    )
    assert state.enabled is False


def test_do_not_track_overrides_file(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={"DO_NOT_TRACK": "1"}, home=tmp_home)
    assert state.enabled is False


# ---------------------------------------------------------------------------
# BERNSTEIN_TELEMETRY env var
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["0", "false", "FALSE", "False", "no", "No", "off", "OFF", ""],
)
def test_bernstein_telemetry_false_values(tmp_home: Path, value: str) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={"BERNSTEIN_TELEMETRY": value}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.ENV


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "True", "ON"])
def test_bernstein_telemetry_truthy_values(tmp_home: Path, value: str) -> None:
    state = resolve(env={"BERNSTEIN_TELEMETRY": value}, home=tmp_home)
    assert state.enabled is True
    assert state.source is OptInSource.ENV


def test_bernstein_telemetry_overrides_file(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={"BERNSTEIN_TELEMETRY": "0"}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.ENV


def test_bernstein_telemetry_whitespace_stripped(tmp_home: Path) -> None:
    state = resolve(env={"BERNSTEIN_TELEMETRY": "  off  "}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.ENV


# ---------------------------------------------------------------------------
# File-based opt-in
# ---------------------------------------------------------------------------


def test_file_enabled_true(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={}, home=tmp_home)
    assert state.enabled is True
    assert state.source is OptInSource.FILE


def test_file_enabled_false(tmp_home: Path) -> None:
    write_enabled(False, home=tmp_home)
    state = resolve(env={}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.FILE


def test_file_missing_falls_to_default(tmp_home: Path) -> None:
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


def test_file_corrupt_falls_to_default(tmp_home: Path) -> None:
    path = tmp_home / ".bernstein" / "telemetry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(":::: not valid yaml ::::", encoding="utf-8")
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


def test_file_unknown_field_falls_to_default(tmp_home: Path) -> None:
    path = tmp_home / ".bernstein" / "telemetry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("something_else: true\n", encoding="utf-8")
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


def test_file_non_bool_value_falls_to_default(tmp_home: Path) -> None:
    path = tmp_home / ".bernstein" / "telemetry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("enabled: maybe\n", encoding="utf-8")
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


def test_file_empty_falls_to_default(tmp_home: Path) -> None:
    path = tmp_home / ".bernstein" / "telemetry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


def test_file_yaml_list_falls_to_default(tmp_home: Path) -> None:
    path = tmp_home / ".bernstein" / "telemetry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- enabled\n- true\n", encoding="utf-8")
    state = resolve(env={}, home=tmp_home)
    assert state.source is OptInSource.DEFAULT


# ---------------------------------------------------------------------------
# Combined precedence
# ---------------------------------------------------------------------------


def test_env_beats_file_off_vs_on(tmp_home: Path) -> None:
    write_enabled(False, home=tmp_home)
    state = resolve(env={"BERNSTEIN_TELEMETRY": "1"}, home=tmp_home)
    assert state.enabled is True
    assert state.source is OptInSource.ENV


def test_env_beats_file_on_vs_off(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(env={"BERNSTEIN_TELEMETRY": "0"}, home=tmp_home)
    assert state.enabled is False
    assert state.source is OptInSource.ENV


def test_do_not_track_beats_bernstein_truthy(tmp_home: Path) -> None:
    state = resolve(
        env={"DO_NOT_TRACK": "1", "BERNSTEIN_TELEMETRY": "1"},
        home=tmp_home,
    )
    assert state.enabled is False
    assert state.source is OptInSource.DO_NOT_TRACK


def test_full_chain_off(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    state = resolve(
        env={"DO_NOT_TRACK": "1", "BERNSTEIN_TELEMETRY": "1"},
        home=tmp_home,
    )
    assert state.enabled is False


def test_write_enabled_creates_dir(tmp_home: Path) -> None:
    target = tmp_home / "fresh"
    write_enabled(True, home=target)
    assert (target / ".bernstein" / "telemetry.yaml").exists()


def test_write_enabled_overwrites(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    write_enabled(False, home=tmp_home)
    state = resolve(env={}, home=tmp_home)
    assert state.enabled is False
