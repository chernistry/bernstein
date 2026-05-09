"""Integration: ``bernstein verify --sigstore`` flag wiring.

Covers:

* ``bernstein verify <wheelhouse> --sigstore`` with no ``gh`` on PATH:
  graceful skip, exit-code unchanged from the cosign / GPG path.
* ``bernstein verify <wheelhouse> --sigstore --require-sigstore`` with
  no ``gh`` on PATH: hard failure, exit-code 1.
* ``bernstein wheelhouse verify <wheelhouse> --sigstore`` mirrors the
  legacy entry point.
* The flag default is OFF; turning it off keeps the legacy behaviour
  identical (additive contract).
* Smoke: ``gh attestation verify`` is invoked once per wheel when the
  binary is mocked-present; the args carry --owner.

These tests stay hermetic by stubbing ``shutil.which`` and
``subprocess.run`` so no network call is made. The end-to-end path
that exercises the public attestations endpoint is covered by the
release-attestation CI smoke job, not this file.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.verify_cmd import verify_cmd
from bernstein.cli.commands.wheelhouse_cmd import wheelhouse_group


@dataclass
class _Fixture:
    path: Path
    wheel_names: tuple[str, ...]


def _write_fake_wheel(target: Path, name: str = "bernstein-1.10.4-py3-none-any.whl") -> Path:
    wheel = target / name
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(f"{name.split('-')[0]}/__init__.py", "VERSION = '1.10.4'\n")
        zf.writestr(
            f"{name.replace('.whl', '')}.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: bernstein\nVersion: 1.10.4\n",
        )
        zf.writestr(f"{name.replace('.whl', '')}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return wheel


def _build_fixture(target: Path) -> _Fixture:
    target.mkdir(parents=True, exist_ok=True)
    wheel = _write_fake_wheel(target)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest = {
        "version": "1.10.4",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "wheels": [{"name": wheel.name, "sha256": digest, "size": wheel.stat().st_size}],
    }
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return _Fixture(path=target, wheel_names=(wheel.name,))


def test_legacy_verify_default_unchanged(tmp_path: Path) -> None:
    """Without --sigstore, the legacy entry point matches the historical contract."""
    fx = _build_fixture(tmp_path / "wh")
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path)])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    # Sigstore section never rendered when the flag is off.
    assert "Sigstore Verify" not in result.output


def test_legacy_verify_sigstore_skips_when_gh_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--sigstore without gh on PATH: skip gracefully, exit 0."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--sigstore"])
    assert result.exit_code == 0, result.output
    assert "Sigstore Verify: SKIPPED" in result.output
    assert "gh CLI not on PATH" in result.output


def test_legacy_verify_require_sigstore_fails_without_gh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--require-sigstore without gh on PATH: hard failure, exit non-zero."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--require-sigstore"])
    assert result.exit_code != 0, result.output
    assert "Sigstore Verify: SKIPPED" in result.output


def test_legacy_verify_sigstore_invokes_gh_with_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--sigstore with mocked gh present invokes ``gh attestation verify`` per wheel."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")
    captured: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, "Verified.\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--sigstore"])
    assert result.exit_code == 0, result.output
    assert "Sigstore Verify: PASSED" in result.output
    assert len(captured) == 1
    args = captured[0]
    assert "attestation" in args
    assert "verify" in args
    assert "--owner" in args


def test_wheelhouse_subcommand_sigstore_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``bernstein wheelhouse verify --sigstore`` mirrors the legacy entry point."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(wheelhouse_group, ["verify", str(fx.path), "--sigstore"])
    assert result.exit_code == 0, result.output
    assert "Sigstore Verify: SKIPPED" in result.output


def test_wheelhouse_subcommand_require_sigstore_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``bernstein wheelhouse verify --require-sigstore`` fails closed without gh."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(wheelhouse_group, ["verify", str(fx.path), "--require-sigstore"])
    assert result.exit_code != 0, result.output


def test_legacy_verify_sigstore_no_attestation_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When gh reports 'no attestations', --sigstore alone skips and exits 0."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "no matching attestations found for digest")

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--sigstore"])
    assert result.exit_code == 0, result.output
    assert "ADVISORY" in result.output


def test_legacy_verify_sigstore_no_attestation_strict_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--require-sigstore`` promotes 'no attestations' from skip to hard failure."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "no matching attestations found for digest")

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--require-sigstore"])
    assert result.exit_code != 0, result.output
    assert "FAILED" in result.output


def test_legacy_verify_sigstore_real_failure_exits_non_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A genuine signature failure exits non-zero even without --require-sigstore."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "failed to verify signature: bad bundle")

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--sigstore"])
    assert result.exit_code != 0, result.output
    assert "Sigstore Verify: FAILED" in result.output


def test_legacy_verify_sigstore_owner_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Custom --sigstore-owner reaches the gh subprocess invocation."""
    fx = _build_fixture(tmp_path / "wh")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")
    captured: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(verify_cmd, [str(fx.path), "--sigstore", "--sigstore-owner", "custom-org"])
    assert result.exit_code == 0, result.output
    assert any("custom-org" in arg for args in captured for arg in args)
