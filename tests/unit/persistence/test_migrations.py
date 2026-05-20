"""Tests for the versioned on-disk state migrations package.

Covers the acceptance criteria from the migrations ticket:

- a v1 -> v2 forward migration applies and advances the stamp;
- re-applying is an idempotent no-op (documented exit code);
- ordering across discovered migrations;
- an unknown-future-version stamp is refused;
- a fresh install stamps the latest version;
- the ``bernstein doctor migrations`` surface lists applied and pending.
"""

from __future__ import annotations

import json

import pytest

from bernstein.core.persistence.migrations import (
    EXIT_APPLIED,
    EXIT_NOOP,
    FutureSchemaVersionError,
    applied_migrations,
    discover_migrations,
    latest_version,
    migrate,
    pending_migrations,
    read_schema_version,
    write_schema_version,
)
from bernstein.core.persistence.migrations import runner as runner_mod


@pytest.fixture
def sdd(tmp_path):
    """Return an empty ``.sdd`` state directory under a temp path."""
    return tmp_path / ".sdd"


# ---------------------------------------------------------------------------
# Discovery and ordering
# ---------------------------------------------------------------------------


def test_discover_migrations_are_ordered():
    migs = discover_migrations()
    assert migs, "at least the baseline migration must be registered"
    versions = [m.version for m in migs]
    assert versions == sorted(versions)
    assert versions[0] == 1
    # No duplicate versions across the package.
    assert len(versions) == len(set(versions))


def test_baseline_is_version_one():
    first = discover_migrations()[0]
    assert first.version == 1
    assert "baseline" in first.module


def test_duplicate_version_is_rejected(tmp_path, monkeypatch):
    """A package with two same-version modules fails fast on discovery."""
    import sys
    import types

    pkg_name = "bernstein.core.persistence.migrations._dupe_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, pkg_name, pkg)

    (tmp_path / "v001_alpha.py").write_text("VERSION = 1\nDESCRIPTION = 'alpha'\ndef apply(p):\n    pass\n")
    (tmp_path / "v001_beta.py").write_text("VERSION = 1\nDESCRIPTION = 'beta'\ndef apply(p):\n    pass\n")

    # Point the runner's package resolution at the fixture package.
    monkeypatch.setattr(runner_mod, "__package__", pkg_name)
    with pytest.raises(RuntimeError, match="duplicate migration version"):
        discover_migrations()


# ---------------------------------------------------------------------------
# Stamp I/O
# ---------------------------------------------------------------------------


def test_missing_stamp_reads_as_zero(sdd):
    assert read_schema_version(sdd) == 0


def test_write_then_read_stamp(sdd):
    write_schema_version(sdd, 2)
    assert read_schema_version(sdd) == 2
    # Stamp file lives at the documented path.
    assert (sdd / ".schema_version").is_file()


def test_corrupt_stamp_reads_as_zero(sdd):
    sdd.mkdir(parents=True)
    (sdd / ".schema_version").write_text("not-a-number")
    assert read_schema_version(sdd) == 0


# ---------------------------------------------------------------------------
# Forward migration v1 -> v2
# ---------------------------------------------------------------------------


def test_migrate_from_v1_to_v2_stamps_runtime_state(sdd):
    # Simulate an install already at v1 with an un-stamped runtime state file.
    runtime = sdd / "runtime"
    runtime.mkdir(parents=True)
    state_file = runtime / "supervisor_state.json"
    state_file.write_text(json.dumps({"restart_count": 3, "current_pid": 42}))
    write_schema_version(sdd, 1)

    report = migrate(sdd, target=2)

    assert report.from_version == 1
    assert report.to_version == 2
    assert report.applied == [2]
    assert report.exit_code == EXIT_APPLIED
    assert read_schema_version(sdd) == 2

    # The v002 migration stamped schema_version into the runtime state file.
    payload = json.loads(state_file.read_text())
    assert payload["schema_version"] == 1
    assert payload["restart_count"] == 3


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_reapply_is_idempotent_noop(sdd):
    migrate(sdd)  # bring fully up to date
    state_before = read_schema_version(sdd)

    report = migrate(sdd)  # re-run

    assert report.applied == []
    assert report.from_version == state_before
    assert report.to_version == state_before
    assert report.exit_code == EXIT_NOOP


def test_v002_apply_is_idempotent(sdd):
    runtime = sdd / "runtime"
    runtime.mkdir(parents=True)
    state_file = runtime / "supervisor_state.json"
    state_file.write_text(json.dumps({"restart_count": 1}))

    from bernstein.core.persistence.migrations import v002_stamp_runtime_state as v2

    v2.apply(sdd)
    first = state_file.read_text()
    v2.apply(sdd)
    second = state_file.read_text()
    assert json.loads(first) == json.loads(second)
    assert json.loads(second)["schema_version"] == 1


# ---------------------------------------------------------------------------
# Unknown future version guard
# ---------------------------------------------------------------------------


def test_future_version_is_refused(sdd):
    write_schema_version(sdd, latest_version() + 5)
    with pytest.raises(FutureSchemaVersionError) as excinfo:
        migrate(sdd)
    assert excinfo.value.stamped == latest_version() + 5
    assert excinfo.value.known_latest == latest_version()


def test_future_version_does_not_touch_state(sdd):
    future = latest_version() + 1
    write_schema_version(sdd, future)
    with pytest.raises(FutureSchemaVersionError):
        migrate(sdd)
    # Stamp untouched.
    assert read_schema_version(sdd) == future


# ---------------------------------------------------------------------------
# Fresh install
# ---------------------------------------------------------------------------


def test_fresh_install_stamps_latest(sdd):
    assert read_schema_version(sdd) == 0
    report = migrate(sdd)
    assert report.from_version == 0
    assert report.to_version == latest_version()
    assert read_schema_version(sdd) == latest_version()
    assert report.exit_code == EXIT_APPLIED


def test_fresh_install_applies_all_migrations_in_order(sdd):
    report = migrate(sdd)
    expected = [m.version for m in discover_migrations()]
    assert report.applied == expected


# ---------------------------------------------------------------------------
# applied / pending split
# ---------------------------------------------------------------------------


def test_pending_then_applied_split(sdd):
    # At v1, everything above v1 is pending.
    write_schema_version(sdd, 1)
    pending = pending_migrations(sdd)
    applied = applied_migrations(sdd)
    assert all(m.version > 1 for m in pending)
    assert all(m.version <= 1 for m in applied)
    # Together they cover the full set.
    assert len(pending) + len(applied) == len(discover_migrations())


# ---------------------------------------------------------------------------
# doctor surface
# ---------------------------------------------------------------------------


def test_doctor_migrations_json_surface(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from bernstein.cli.commands.doctor.migrations import migrations_cmd

    monkeypatch.chdir(tmp_path)
    # Pre-stamp at v1 so there is at least one pending migration to show.
    write_schema_version(tmp_path / ".sdd", 1)

    runner = CliRunner()
    result = runner.invoke(migrations_cmd, ["--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["latest_version"] == latest_version()
    assert any(p["version"] == 1 for p in payload["applied"])
    if latest_version() > 1:
        assert any(p["version"] > 1 for p in payload["pending"])


def test_doctor_migrations_apply_advances_stamp(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from bernstein.cli.commands.doctor.migrations import migrations_cmd

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(migrations_cmd, ["--apply", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == latest_version()
    assert payload["pending"] == []
