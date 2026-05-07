"""Adversarial tests for the air-gap distribution stack.

Sovereign customers run :command:`bernstein verify` on every wheelhouse
before they install it on an isolated host. The verify routine is the
last line of defence, so it has to refuse a hostile manifest the same
way a SAST tool refuses a hostile source tree -- by enumerating every
offence rather than blindly trusting the input.

This file complements the integration tests in
``tests/integration/test_airgap_wheelhouse.py`` with deterministic
unit tests that target individual failure modes:

- path traversal / absolute-path / drive-letter wheel names in
  ``MANIFEST.json`` (a tampered manifest could otherwise cause the
  verifier to read ``/etc/passwd``)
- symlink-based wheel substitution (TOCTOU: hash a symlink target,
  pip-install the swapped target later)
- ``--profile airgap`` combined with ``--allow-network any`` (the
  combination silently disables the air-gap boundary, so we reject it
  at parse time)
- bracketed IPv6 host:port tokens in the network policy parser
- multi-flag ``--allow-network`` semantics
- malformed manifest input (non-string sha256, control characters
  in names, empty entries)
- cosign / GPG verifier behaviour when the offered signature comes
  from the wrong key

Tests never touch the real network -- every signing oracle and wheel
content is constructed in-process and mocked where the verifier
would otherwise shell out.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from bernstein.core.distribution.verifier import (
    CosignVerifier,
    GpgVerifier,
    _is_safe_wheel_name,
    verify_wheelhouse,
)
from bernstein.core.security.network_policy import (
    ENV_NETWORK_POLICY,
    ENV_PROFILE_MODE,
    PROFILE_AIRGAP,
    NetworkPolicy,
    NetworkPolicyConfigError,
    NetworkPolicyDenied,
    policy_from_env,
)

# ---------------------------------------------------------------------------
# Wheel fixture helpers
# ---------------------------------------------------------------------------


def _write_wheel(target: Path, name: str = "pkg-1.0-py3-none-any.whl") -> Path:
    wheel = target / name
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("pkg/__init__.py", "x = 1\n")
    return wheel


def _make_minimal_wheelhouse(
    target: Path,
    *,
    names: tuple[str, ...] = ("pkg-1.0-py3-none-any.whl",),
) -> dict[str, str]:
    target.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    shas: dict[str, str] = {}
    for name in names:
        wheel = _write_wheel(target, name)
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        entries.append({"name": name, "sha256": sha, "size": wheel.stat().st_size})
        shas[name] = sha
    (target / "MANIFEST.json").write_text(
        json.dumps({"version": "1.0", "wheels": entries}, indent=2, sort_keys=True) + "\n"
    )
    return shas


# ---------------------------------------------------------------------------
# Manifest path traversal / hostile names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    [
        "/etc/passwd",
        "../../etc/passwd",
        "../escape.whl",
        "subdir/wheel.whl",
        "C:\\Windows\\system32\\config\\sam",
        "name\x00.whl",
        "name\nwith-newline.whl",
        "name\rwith-cr.whl",
        "",
    ],
)
def test_is_safe_wheel_name_rejects_hostile_inputs(hostile_name: str) -> None:
    assert _is_safe_wheel_name(hostile_name) is False, f"{hostile_name!r} should be rejected"


@pytest.mark.parametrize(
    "safe_name",
    [
        "pkg-1.0-py3-none-any.whl",
        "click-8.1.7-py3-none-any.whl",
        "name_with_underscores-1.0.tar.gz",
    ],
)
def test_is_safe_wheel_name_accepts_normal_inputs(safe_name: str) -> None:
    assert _is_safe_wheel_name(safe_name) is True


def test_verify_rejects_absolute_path_in_manifest(tmp_path: Path) -> None:
    """A tampered manifest naming ``/etc/passwd`` must not cause the
    verifier to read system files."""
    wh = tmp_path / "wh"
    wh.mkdir()
    decoy = _write_wheel(wh)
    sha = hashlib.sha256(decoy.read_bytes()).hexdigest()
    manifest = {
        "version": "1.0",
        "wheels": [
            {"name": "/etc/passwd", "sha256": sha, "size": decoy.stat().st_size},
            {"name": decoy.name, "sha256": sha, "size": decoy.stat().st_size},
        ],
    }
    (wh / "MANIFEST.json").write_text(json.dumps(manifest))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("unsafe wheel name" in f and "/etc/passwd" in f for f in report.failures)
    # The verify routine must not have hashed /etc/passwd by accident.
    assert not any("sha256 mismatch" in f and "/etc/passwd" in f for f in report.failures)


def test_verify_rejects_traversal_in_manifest(tmp_path: Path) -> None:
    wh = tmp_path / "wh"
    wh.mkdir()
    manifest = {
        "version": "1.0",
        "wheels": [{"name": "../../etc/passwd", "sha256": "a" * 64, "size": 0}],
    }
    (wh / "MANIFEST.json").write_text(json.dumps(manifest))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("unsafe wheel name" in f for f in report.failures)


def test_verify_rejects_subdir_in_manifest(tmp_path: Path) -> None:
    """Subdirectory entries (``subdir/wheel.whl``) are rejected -- the
    bundle layout is intentionally flat."""
    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "subdir").mkdir()
    inner = _write_wheel(wh / "subdir")
    sha = hashlib.sha256(inner.read_bytes()).hexdigest()
    manifest = {
        "version": "1.0",
        "wheels": [{"name": f"subdir/{inner.name}", "sha256": sha, "size": 1}],
    }
    (wh / "MANIFEST.json").write_text(json.dumps(manifest))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("unsafe wheel name" in f for f in report.failures)


def test_verify_rejects_symlink_wheel(tmp_path: Path) -> None:
    """A wheel that is a symlink is rejected to avoid TOCTOU swapping
    between hash-time and pip-install time."""
    wh = tmp_path / "wh"
    wh.mkdir()
    real = tmp_path / "real.whl"
    real.write_bytes(b"GOOD")
    sha = hashlib.sha256(b"GOOD").hexdigest()
    link = wh / "pkg-1.0-py3-none-any.whl"
    link.symlink_to(real)
    manifest = {
        "version": "1.0",
        "wheels": [{"name": link.name, "sha256": sha, "size": 4}],
    }
    (wh / "MANIFEST.json").write_text(json.dumps(manifest))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("symlink wheel rejected" in f for f in report.failures)


def test_verify_rejects_symlink_manifest(tmp_path: Path) -> None:
    """A MANIFEST.json that is itself a symlink is rejected -- same TOCTOU
    concern, plus an attacker-controlled manifest body."""
    wh = tmp_path / "wh"
    wh.mkdir()
    real = tmp_path / "evil.json"
    real.write_text('{"version":"1.0","wheels":[]}')
    (wh / "MANIFEST.json").symlink_to(real)
    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("MANIFEST.json is a symlink" in f for f in report.failures)


def test_verify_enumerates_every_offence(tmp_path: Path) -> None:
    """Tampered + hostile name should both surface; the verifier must
    not short-circuit on the first failure."""
    wh = tmp_path / "wh"
    wh.mkdir()
    good = _write_wheel(wh, "good-1.0-py3-none-any.whl")
    sha = hashlib.sha256(good.read_bytes()).hexdigest()
    tampered = _write_wheel(wh, "tampered-1.0-py3-none-any.whl")
    tampered.write_bytes(tampered.read_bytes() + b"TAMPER")
    manifest = {
        "version": "1.0",
        "wheels": [
            {"name": good.name, "sha256": sha, "size": good.stat().st_size},
            {"name": tampered.name, "sha256": "a" * 64, "size": tampered.stat().st_size},
            {"name": "/etc/passwd", "sha256": "b" * 64, "size": 0},
            {"name": "../traverse.whl", "sha256": "c" * 64, "size": 0},
        ],
    }
    (wh / "MANIFEST.json").write_text(json.dumps(manifest))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("sha256 mismatch" in f and tampered.name in f for f in report.failures)
    assert any("unsafe wheel name" in f and "/etc/passwd" in f for f in report.failures)
    assert any("unsafe wheel name" in f and "../traverse.whl" in f for f in report.failures)


# ---------------------------------------------------------------------------
# Verifier behaviour -- wrong key / unavailable backend
# ---------------------------------------------------------------------------


def test_cosign_verifier_returns_false_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cosign exit code != 0 (wrong key, no entry in transparency log)
    must surface as ``verify() -> False`` so the caller fails the run."""

    class _FailedRun:
        returncode = 1
        stderr = "no matching signature"

    monkeypatch.setattr("bernstein.core.distribution.verifier.shutil.which", lambda _name: "/usr/bin/cosign")
    monkeypatch.setattr(
        "bernstein.core.distribution.verifier.subprocess.run",
        lambda *_a, **_kw: _FailedRun(),
    )
    blob = tmp_path / "wheel"
    blob.write_bytes(b"contents")
    sig = tmp_path / "wheel.sig"
    sig.write_bytes(b"valid-format-but-wrong-key")
    v = CosignVerifier(pubkey_path=tmp_path / "release.pub")
    assert v.verify(blob, sig) is False


def test_gpg_verifier_returns_false_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FailedRun:
        returncode = 1
        stderr = "BAD signature"

    monkeypatch.setattr("bernstein.core.distribution.verifier.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "bernstein.core.distribution.verifier.subprocess.run",
        lambda *_a, **_kw: _FailedRun(),
    )
    blob = tmp_path / "wheel"
    blob.write_bytes(b"contents")
    sig = tmp_path / "wheel.sig"
    sig.write_bytes(b"valid-format-but-wrong-keyring")
    v = GpgVerifier()
    assert v.verify(blob, sig) is False


def test_cosign_verifier_swallows_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A hung cosign process must not crash verify_wheelhouse; the verifier
    swallows :class:`subprocess.TimeoutExpired` and reports failure."""
    import subprocess

    monkeypatch.setattr("bernstein.core.distribution.verifier.shutil.which", lambda _n: "/usr/bin/cosign")

    def _hang(*_a: object, **_kw: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["cosign"], timeout=30)

    monkeypatch.setattr("bernstein.core.distribution.verifier.subprocess.run", _hang)
    blob = tmp_path / "blob"
    blob.write_bytes(b"x")
    sig = tmp_path / "blob.sig"
    sig.write_bytes(b"x")
    v = CosignVerifier(pubkey_path=tmp_path / "k.pub")
    assert v.verify(blob, sig) is False


def test_verifier_signature_mismatch_produces_failures(tmp_path: Path) -> None:
    """End-to-end: when a bogus signature is reported invalid by the
    chosen verifier, the report enumerates the offending wheel."""
    wh = tmp_path / "wh"
    _make_minimal_wheelhouse(wh)
    wheel = wh / "pkg-1.0-py3-none-any.whl"
    sig = wheel.with_suffix(wheel.suffix + ".sig")
    sig.write_bytes(b"signed-by-wrong-key")

    # Stub a verifier that always rejects, like cosign with a different key.
    class _AlwaysFail:
        name = "cosign"

        def available(self) -> bool:
            return True

        def verify(self, _blob: Path, _signature: Path) -> bool:
            return False

    report = verify_wheelhouse(wh, verifier=_AlwaysFail())
    assert report.ok is False
    assert any("signature invalid" in f and wheel.name in f for f in report.failures)


# ---------------------------------------------------------------------------
# Network policy edge cases
# ---------------------------------------------------------------------------


def test_network_policy_bracketed_ipv6_host_only() -> None:
    p = NetworkPolicy.from_specs(("[::1]",))
    assert p.is_allowed("::1", 8080) is True
    assert p.is_allowed("2001:db8::1", 8080) is False


def test_network_policy_bracketed_ipv6_with_port() -> None:
    p = NetworkPolicy.from_specs(("[2001:db8::1]:443",))
    assert p.is_allowed("2001:db8::1", 443) is True
    assert p.is_allowed("2001:db8::1", 80) is False


def test_network_policy_cidr_zero_allows_everything_on_purpose() -> None:
    """Operators sometimes type ``0.0.0.0/0`` thinking it's a placeholder.
    The policy honours it (a CIDR is a CIDR), but the airgap profile
    rejects ``--allow-network any`` so the only path here is an explicit
    operator choice outside airgap. Document it with a regression test."""
    p = NetworkPolicy.from_specs(("0.0.0.0/0",))
    assert p.is_allowed("8.8.8.8", 443) is True
    assert p.is_allowed("203.0.113.5", 443) is True
    # IPv6 not covered by an IPv4 /0.
    assert p.is_allowed("2001:db8::1", 443) is False


def test_network_policy_v6_zero_cidr() -> None:
    p = NetworkPolicy.from_specs(("::/0",))
    assert p.is_allowed("2001:db8::1", 443) is True
    assert p.is_allowed("8.8.8.8", 443) is False


def test_network_policy_any_overrides_other_specs() -> None:
    """Multi-flag merge: ``--allow-network any --allow-network 127.0.0.1``
    yields ``allow_any=True``. This is documented behaviour but it's
    DANGEROUS under ``--profile airgap`` -- see
    :func:`test_install_policy_airgap_rejects_any_override` for the
    parse-time guard."""
    p = NetworkPolicy.from_specs(("any", "127.0.0.1"))
    assert p.allow_any is True


def test_network_policy_uppercase_any_matches() -> None:
    """``ANY`` and ``Any`` are accepted; only ``any`` (case-insensitive)
    short-circuits to allow-all."""
    assert NetworkPolicy.from_specs(("ANY",)).allow_any is True
    assert NetworkPolicy.from_specs(("Any",)).allow_any is True


def test_network_policy_uppercase_none_matches() -> None:
    """Same case-insensitivity for ``none``."""
    assert NetworkPolicy.from_specs(("NONE",)).allow_any is False
    assert NetworkPolicy.from_specs(("None",)).allow_any is False


def test_network_policy_none_plus_explicit_host_uses_host() -> None:
    """When the operator passes both ``none`` and a host, the explicit
    host wins (``none`` is implied by the absence of allow-list entries
    so listing it alongside an entry is a no-op)."""
    p = NetworkPolicy.from_specs(("none", "127.0.0.1"))
    assert p.allow_any is False
    assert p.is_allowed("127.0.0.1", 8080) is True
    assert p.is_allowed("api.cloudflare.com", 443) is False


def test_network_policy_whitespace_only_is_allow_all() -> None:
    """Empty / whitespace-only specs collapse to allow-all (back-compat
    default outside ``--profile airgap``)."""
    assert NetworkPolicy.from_specs(("  ", "\t")).allow_any is True


def test_network_policy_check_url_empty_string_denies() -> None:
    """``check_url('')`` must not crash; the empty hostname is denied."""
    p = NetworkPolicy.from_specs(("127.0.0.1",))
    with pytest.raises(NetworkPolicyDenied):
        p.check_url("")


def test_policy_from_env_treats_empty_as_allow_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NETWORK_POLICY, "")
    assert policy_from_env().allow_any is True


def test_policy_from_env_strips_whitespace_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NETWORK_POLICY, "127.0.0.1 , 10.0.0.0/8 ")
    p = policy_from_env()
    assert p.is_allowed("127.0.0.1", 80) is True
    assert p.is_allowed("10.5.5.5", 443) is True


def test_network_policy_dns_loopback_aliases() -> None:
    """``localhost`` matches ``127.0.0.1`` and ``::1``; the converse
    holds too. This is the ``--allow-network none`` does NOT accidentally
    allow DNS resolution invariant -- DNS queries route to a host, and
    the host is the one that gets policy-checked."""
    p = NetworkPolicy.from_specs(("localhost",))
    assert p.is_allowed("127.0.0.1", 53) is True
    assert p.is_allowed("::1", 53) is True
    assert p.is_allowed("8.8.8.8", 53) is False


# ---------------------------------------------------------------------------
# Adapter-level: declare-or-fail-closed is policy-relative
# ---------------------------------------------------------------------------


def test_adapter_without_declared_endpoints_is_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """An adapter that declares no endpoints is treated as a local
    subprocess and ``enforce_network_policy`` is a no-op. This is the
    documented contract -- adapters that DO dial out are required to
    declare their endpoints. We assert the behaviour to lock it in."""
    monkeypatch.setenv(ENV_NETWORK_POLICY, "none")
    from bernstein.adapters.base import CLIAdapter

    class _Local(CLIAdapter):
        # external_endpoints inherited as empty tuple.
        def name(self) -> str:
            return "local"

        def spawn(self, **_kw: object) -> object:  # type: ignore[override]
            raise NotImplementedError

    adapter = _Local()
    # No exception even under deny-all, because the adapter has no
    # declared external endpoints.
    adapter.enforce_network_policy()


def test_adapter_with_declared_endpoint_blocks_under_deny_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NETWORK_POLICY, "none")
    from bernstein.adapters.base import CLIAdapter

    class _Cloud(CLIAdapter):
        external_endpoints = (("api.example.com", 443),)

        def name(self) -> str:
            return "cloud"

        def spawn(self, **_kw: object) -> object:  # type: ignore[override]
            raise NotImplementedError

    with pytest.raises(NetworkPolicyDenied) as excinfo:
        _Cloud().enforce_network_policy()
    assert "api.example.com:443" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Profile install -- refuse silent airgap escape
# ---------------------------------------------------------------------------


def test_install_policy_airgap_rejects_any_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--profile airgap --allow-network any`` would silently disable
    the air-gap boundary. The installer rejects the combination at parse
    time so the operator cannot accidentally typo their way out of
    air-gap mode."""
    monkeypatch.delenv(ENV_NETWORK_POLICY, raising=False)
    monkeypatch.delenv(ENV_PROFILE_MODE, raising=False)
    from bernstein.cli.run_bootstrap import _install_network_policy

    with pytest.raises(NetworkPolicyConfigError):
        _install_network_policy(run_profile=PROFILE_AIRGAP, allow_network=("any",))

    with pytest.raises(NetworkPolicyConfigError):
        _install_network_policy(run_profile=PROFILE_AIRGAP, allow_network=("127.0.0.1", "any"))

    # Case-insensitive: ``ANY`` and ``Any`` rejected too.
    with pytest.raises(NetworkPolicyConfigError):
        _install_network_policy(run_profile=PROFILE_AIRGAP, allow_network=("ANY",))


def test_install_policy_airgap_accepts_specific_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legitimate way to open holes under airgap is to list
    specific destinations."""
    monkeypatch.delenv(ENV_NETWORK_POLICY, raising=False)
    monkeypatch.delenv(ENV_PROFILE_MODE, raising=False)
    from bernstein.cli.run_bootstrap import _install_network_policy

    _install_network_policy(
        run_profile=PROFILE_AIRGAP,
        allow_network=("127.0.0.1", "10.0.0.0/8", "ollama.local:11434"),
    )
    p = policy_from_env()
    assert p.allow_any is False
    assert p.is_allowed("127.0.0.1", 8080) is True
    assert p.is_allowed("10.5.5.5", 443) is True
    assert p.is_allowed("ollama.local", 11434) is True
    assert p.is_allowed("api.openai.com", 443) is False


def test_install_policy_non_airgap_still_allows_any(monkeypatch: pytest.MonkeyPatch) -> None:
    """``any`` is only rejected when the operator explicitly opted into
    ``--profile airgap``. Outside the profile it is the back-compat
    default and remains a valid escape hatch."""
    monkeypatch.delenv(ENV_NETWORK_POLICY, raising=False)
    monkeypatch.delenv(ENV_PROFILE_MODE, raising=False)
    from bernstein.cli.run_bootstrap import _install_network_policy

    _install_network_policy(run_profile=None, allow_network=("any",))
    assert policy_from_env().allow_any is True


# ---------------------------------------------------------------------------
# Manifest schema robustness
# ---------------------------------------------------------------------------


def test_verify_handles_unknown_manifest_version_field(tmp_path: Path) -> None:
    """A future manifest schema bump (``manifest_version: 99``) must
    not crash the current verifier -- it just ignores the unknown
    field and trusts the wheels list."""
    wh = tmp_path / "wh"
    _make_minimal_wheelhouse(wh)
    manifest_path = wh / "MANIFEST.json"
    payload = json.loads(manifest_path.read_text())
    payload["manifest_version"] = 99
    payload["future_field"] = {"opaque": True}
    manifest_path.write_text(json.dumps(payload))

    report = verify_wheelhouse(wh)
    assert report.ok is True


def test_verify_handles_non_string_sha(tmp_path: Path) -> None:
    """A manifest entry whose sha256 is a number / list / null must be
    flagged ``malformed manifest entry`` rather than crashing the loop."""
    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "MANIFEST.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "wheels": [
                    {"name": "a.whl", "sha256": 12345, "size": 0},
                    {"name": "b.whl", "sha256": None, "size": 0},
                    {"name": "c.whl", "sha256": ["array"], "size": 0},
                ],
            }
        )
    )
    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert sum("manifest entry malformed" in f for f in report.failures) == 3


def test_verify_handles_wheels_field_not_list(tmp_path: Path) -> None:
    """``wheels`` typed as a dict instead of a list must surface a
    'no wheels' failure, not crash."""
    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "MANIFEST.json").write_text(json.dumps({"version": "1.0", "wheels": {"oops": "wrong"}}))

    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("no wheels" in f.lower() for f in report.failures)


def test_verify_handles_truncated_json(tmp_path: Path) -> None:
    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "MANIFEST.json").write_text('{"version": "1.0", "wheels":')
    report = verify_wheelhouse(wh)
    assert report.ok is False
    assert any("malformed MANIFEST.json" in f for f in report.failures)
