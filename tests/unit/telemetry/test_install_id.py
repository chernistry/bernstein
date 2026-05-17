"""Install id lifecycle tests.

Invariants:
- Never generated before opt-in.
- Generated exactly once after opt-in.
- Removed on opt-out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bernstein.core.telemetry import (
    ensure_install_id,
    install_id_path,
    read_install_id,
    reset_install_id,
    write_enabled,
)

_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def test_read_returns_none_when_missing(tmp_home: Path) -> None:
    assert read_install_id(home=tmp_home) is None


def test_ensure_raises_when_disabled(tmp_home: Path) -> None:
    with pytest.raises(RuntimeError):
        ensure_install_id(home=tmp_home)


def test_ensure_succeeds_after_opt_in(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    install_id = ensure_install_id(home=tmp_home)
    assert _HEX_RE.match(install_id)


def test_ensure_is_idempotent(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    a = ensure_install_id(home=tmp_home)
    b = ensure_install_id(home=tmp_home)
    assert a == b


def test_ensure_persists_to_disk(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    install_id = ensure_install_id(home=tmp_home)
    on_disk = install_id_path(home=tmp_home).read_text(encoding="utf-8").strip()
    assert on_disk == install_id


def test_ensure_creates_directory(tmp_home: Path) -> None:
    fresh = tmp_home / "fresh"
    write_enabled(True, home=fresh)
    ensure_install_id(home=fresh)
    assert (fresh / ".bernstein" / "install-id").exists()


def test_reset_removes_file(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    ensure_install_id(home=tmp_home)
    reset_install_id(home=tmp_home)
    assert read_install_id(home=tmp_home) is None


def test_reset_on_missing_is_noop(tmp_home: Path) -> None:
    # Must not raise.
    reset_install_id(home=tmp_home)


def test_read_returns_none_for_empty_file(tmp_home: Path) -> None:
    path = install_id_path(home=tmp_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert read_install_id(home=tmp_home) is None


def test_default_state_no_file(tmp_home: Path) -> None:
    """The critical invariant: default-off MUST NOT generate an id."""
    # Trigger nothing.  No write_enabled, no env.
    assert not install_id_path(home=tmp_home).exists()


def test_ensure_blocked_by_explicit_env_opt_out(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with file=true, BERNSTEIN_TELEMETRY=0 blocks generation."""
    write_enabled(True, home=tmp_home)
    monkeypatch.setenv("BERNSTEIN_TELEMETRY", "0")
    with pytest.raises(RuntimeError):
        ensure_install_id(home=tmp_home)


def test_ensure_blocked_by_do_not_track(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_enabled(True, home=tmp_home)
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    with pytest.raises(RuntimeError):
        ensure_install_id(home=tmp_home)


def test_install_id_uniqueness_across_fresh_homes(tmp_path: Path) -> None:
    ids: set[str] = set()
    for i in range(8):
        h = tmp_path / f"h{i}"
        write_enabled(True, home=h)
        ids.add(ensure_install_id(home=h))
    assert len(ids) == 8
