"""Unit tests for the Sigstore-attestation verifier.

Covers:

* :class:`SigstoreAttestationVerifier.available` returns False when the
  ``gh`` CLI is missing on PATH.
* :func:`verify_artefacts_with_sigstore` short-circuits with a clean
  "skip" report when ``gh`` is absent and ``require_attestation=False``.
* The same call hard-fails when ``require_attestation=True`` -- this is
  the strict-mode contract operators rely on.
* The verifier returns ``ok=False`` on a non-existent artefact and
  ``ok=None`` (skip, with a clear reason) when the artefact has no
  attestation in the Rekor log.
* Offline mode resolves a sibling ``.sigstore`` bundle and skips with a
  clear reason when none is found alongside the artefact.

The tests stay hermetic: they monkeypatch ``shutil.which`` and
``subprocess.run`` so no network call escapes the suite. The
attestation flow is exercised end-to-end against the GitHub
attestations endpoint by a separate CI smoke job (see ``ci.yml`` /
``release-attestation`` workflow), not by this unit file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.distribution.sigstore_attestation_verify import (
    DEFAULT_OWNER,
    SigstoreAttestationVerifier,
    SigstoreVerifyOutcome,
    verify_artefacts_with_sigstore,
)


def _no_gh(_name: str) -> str | None:
    """``shutil.which`` substitute that pretends ``gh`` is absent."""
    return None


def _yes_gh(_name: str) -> str:
    """``shutil.which`` substitute that pretends ``gh`` is on PATH."""
    return "/usr/local/bin/gh"


@pytest.fixture
def fake_wheel(tmp_path: Path) -> Path:
    """Return a path to a one-byte fixture wheel."""
    wheel = tmp_path / "bernstein-1.10.4-py3-none-any.whl"
    wheel.write_bytes(b"\x00")
    return wheel


def test_default_owner_matches_release_owner() -> None:
    """The default owner must match the GitHub org running the publish workflow."""
    assert DEFAULT_OWNER == "sipyourdrink-ltd"


def test_available_false_when_gh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifier reports unavailable when the ``gh`` binary is not on PATH."""
    monkeypatch.setattr("shutil.which", _no_gh)
    v = SigstoreAttestationVerifier()
    assert v.available() is False


def test_available_true_when_gh_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifier reports available when the ``gh`` binary is on PATH."""
    monkeypatch.setattr("shutil.which", _yes_gh)
    v = SigstoreAttestationVerifier()
    assert v.available() is True


def test_verify_missing_artefact_returns_failure(tmp_path: Path) -> None:
    """A non-existent artefact is a hard verifier failure with a clear reason."""
    v = SigstoreAttestationVerifier()
    outcome = v.verify(tmp_path / "does-not-exist.whl")
    assert isinstance(outcome, SigstoreVerifyOutcome)
    assert outcome.ok is False
    assert "does not exist" in outcome.reason


def test_verify_skips_when_gh_missing(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """Without ``gh`` on PATH the verifier returns ok=None, never True / False."""
    monkeypatch.setattr("shutil.which", _no_gh)
    v = SigstoreAttestationVerifier()
    outcome = v.verify(fake_wheel)
    assert outcome.ok is None
    assert "gh CLI not on PATH" in outcome.reason


def test_verify_recognises_no_attestation_response(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """When ``gh`` reports "no attestations" the verifier reports a skip, not a failure."""
    monkeypatch.setattr("shutil.which", _yes_gh)

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="no matching attestations found for digest sha256:...",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    v = SigstoreAttestationVerifier()
    outcome = v.verify(fake_wheel)
    assert outcome.ok is None
    assert "no Sigstore attestation" in outcome.reason


def test_verify_returns_failure_on_non_zero_other_than_no_attestation(
    monkeypatch: pytest.MonkeyPatch, fake_wheel: Path
) -> None:
    """A real ``gh`` error (signature mismatch, etc.) is a hard failure."""
    monkeypatch.setattr("shutil.which", _yes_gh)

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="failed to verify signature: not a valid bundle",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    v = SigstoreAttestationVerifier()
    outcome = v.verify(fake_wheel)
    assert outcome.ok is False
    assert "rc=1" in outcome.reason


def test_verify_returns_pass_on_zero_exit(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """A zero-exit ``gh attestation verify`` produces ok=True with the owner in the reason."""
    monkeypatch.setattr("shutil.which", _yes_gh)

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Loaded digest sha256:...\nVerified attestation by Bernstein release pipeline.\n",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    v = SigstoreAttestationVerifier(owner="sipyourdrink-ltd")
    outcome = v.verify(fake_wheel)
    assert outcome.ok is True
    assert "owner=sipyourdrink-ltd" in outcome.reason


def test_verify_offline_skips_when_no_bundle(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """Offline mode without a sibling ``.sigstore`` bundle reports an honest skip."""
    monkeypatch.setattr("shutil.which", _yes_gh)
    v = SigstoreAttestationVerifier(offline=True)
    outcome = v.verify(fake_wheel)
    assert outcome.ok is None
    assert "no .sigstore bundle" in outcome.reason


def test_verify_offline_uses_sibling_bundle(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """Offline mode passes a sibling bundle to ``gh`` via --bundle."""
    monkeypatch.setattr("shutil.which", _yes_gh)
    bundle = fake_wheel.with_suffix(fake_wheel.suffix + ".sigstore")
    bundle.write_text("{}")

    captured: dict[str, list[str]] = {"args": []}

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    v = SigstoreAttestationVerifier(offline=True)
    outcome = v.verify(fake_wheel)
    assert outcome.ok is True
    assert "--bundle" in captured["args"]
    assert str(bundle) in captured["args"]


def test_verify_timeout_returns_skip(monkeypatch: pytest.MonkeyPatch, fake_wheel: Path) -> None:
    """Network timeout is treated as a skip, not a hard failure (graceful fallback)."""
    monkeypatch.setattr("shutil.which", _yes_gh)

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="gh", timeout=60)

    monkeypatch.setattr("subprocess.run", fake_run)
    v = SigstoreAttestationVerifier(timeout_s=60)
    outcome = v.verify(fake_wheel)
    assert outcome.ok is None
    assert "timed out" in outcome.reason


def test_batch_skips_when_gh_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The batch-level helper reports the gh-missing case as ok=None when not strict."""
    monkeypatch.setattr("shutil.which", _no_gh)
    wheel = tmp_path / "x-1.0-py3-none-any.whl"
    wheel.write_bytes(b"x")
    report = verify_artefacts_with_sigstore([wheel])
    assert report.verifier_available is False
    assert report.ok is None
    assert any("gh CLI not on PATH" in s for s in report.skips)


def test_batch_strict_mode_promotes_skip_to_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``require_attestation=True`` promotes any skip to a hard failure (strict mode)."""
    monkeypatch.setattr("shutil.which", _no_gh)
    wheel = tmp_path / "x-1.0-py3-none-any.whl"
    wheel.write_bytes(b"x")
    report = verify_artefacts_with_sigstore([wheel], require_attestation=True)
    assert report.ok is False
    assert any("gh CLI not on PATH" in f for f in report.failures)


def test_batch_aggregates_pass_skip_and_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The batch report enumerates every offender / skipper without short-circuiting."""
    monkeypatch.setattr("shutil.which", _yes_gh)
    pass_wheel = tmp_path / "pass-1.0-py3-none-any.whl"
    pass_wheel.write_bytes(b"a")
    skip_wheel = tmp_path / "skip-1.0-py3-none-any.whl"
    skip_wheel.write_bytes(b"b")
    fail_wheel = tmp_path / "fail-1.0-py3-none-any.whl"
    fail_wheel.write_bytes(b"c")

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        target = args[3] if len(args) > 3 else ""
        if "pass-" in target:
            return subprocess.CompletedProcess(args, 0, "ok", "")
        if "skip-" in target:
            return subprocess.CompletedProcess(args, 1, "", "no matching attestations found")
        return subprocess.CompletedProcess(args, 1, "", "verify failed: bad cert")

    monkeypatch.setattr("subprocess.run", fake_run)

    report = verify_artefacts_with_sigstore([pass_wheel, skip_wheel, fail_wheel])
    assert report.verifier_available is True
    assert report.ok is False  # at least one hard failure
    assert report.passes == 1
    assert any("fail-" in f for f in report.failures)
    assert any("skip-" in s for s in report.skips)


def test_batch_pure_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All artefacts attested -> ok=True, empty failures and skips."""
    monkeypatch.setattr("shutil.which", _yes_gh)

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    wheels = []
    for i in range(3):
        w = tmp_path / f"x-1.{i}-py3-none-any.whl"
        w.write_bytes(b"y")
        wheels.append(w)
    report = verify_artefacts_with_sigstore(wheels)
    assert report.ok is True
    assert report.passes == 3
    assert report.failures == ()
    assert report.skips == ()
