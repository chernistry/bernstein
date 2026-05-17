"""Unit tests for ``bernstein.core.autoheal.kill_switch``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bernstein.core.autoheal.kill_switch import read


def test_missing_file_returns_enabled(tmp_path: Path) -> None:
    state = read(tmp_path / "absent")
    assert state.disabled is False
    assert state.reason == "no_file"


def test_empty_file_returns_enabled(tmp_path: Path) -> None:
    p = tmp_path / "ks"
    p.write_text("", encoding="utf-8")
    state = read(p)
    assert state.disabled is False
    assert state.reason == "empty_file"


def test_forever_disables(tmp_path: Path) -> None:
    p = tmp_path / "ks"
    p.write_text("forever\n", encoding="utf-8")
    state = read(p)
    assert state.disabled is True
    assert state.reason == "forever"


def test_forever_case_insensitive(tmp_path: Path) -> None:
    p = tmp_path / "ks"
    p.write_text("FOREVER", encoding="utf-8")
    state = read(p)
    assert state.disabled is True


def test_future_iso_disables(tmp_path: Path) -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    future = now + timedelta(hours=1)
    p = tmp_path / "ks"
    p.write_text(future.isoformat(), encoding="utf-8")
    state = read(p, now=now)
    assert state.disabled is True
    assert "until:" in state.reason


def test_past_iso_does_not_disable(tmp_path: Path) -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    past = now - timedelta(hours=1)
    p = tmp_path / "ks"
    p.write_text(past.isoformat(), encoding="utf-8")
    state = read(p, now=now)
    assert state.disabled is False
    assert state.reason.startswith("expired_at:")


def test_trailing_z_iso_accepted(tmp_path: Path) -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    future = now + timedelta(hours=1)
    iso_z = future.isoformat().replace("+00:00", "Z")
    p = tmp_path / "ks"
    p.write_text(iso_z, encoding="utf-8")
    state = read(p, now=now)
    assert state.disabled is True


def test_naive_iso_is_treated_as_utc(tmp_path: Path) -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    p = tmp_path / "ks"
    p.write_text("2026-05-17T13:00:00", encoding="utf-8")
    state = read(p, now=now)
    assert state.disabled is True


def test_unparseable_content_fails_safe(tmp_path: Path) -> None:
    p = tmp_path / "ks"
    p.write_text("never-on-no-explanation", encoding="utf-8")
    state = read(p)
    # Fail-safe: unrecognised content disables auto-heal until human fix.
    assert state.disabled is True
    assert state.reason.startswith("unparseable:")
