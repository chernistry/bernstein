"""Unit tests for the CLM mTLS launcher shim.

Covers the pieces of :mod:`bernstein.adapters.clm_tls_launcher` that
don't require aider on PATH: env-var resolution, the
``httpx.Client`` / ``httpx.AsyncClient`` monkey-patch, and the
defensive guard against being asked to launch something other than
aider. The end-to-end TLS handshake is exercised separately by
``tests/integration/adapters/test_adapter_clm_with_fake_nim.py``.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bernstein.adapters import clm_tls_launcher
from bernstein.adapters.clm import (
    CLM_CA_FILE_ENV,
    CLM_CERT_FILE_ENV,
    CLM_KEY_FILE_ENV,
)
from bernstein.core.protocols.cluster.cluster_tls import TLSConfig


def _make_pki(out_dir: Path) -> dict[str, Path]:
    """Tiny self-signed CA + leaf cert/key trio for httpx-patch sanity."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "client")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = {
        "ca": out_dir / "ca.crt",
        "cert": out_dir / "client.crt",
        "key": out_dir / "client.key",
    }
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return paths


@pytest.fixture
def httpx_init_restored() -> object:
    """Snapshot+restore ``httpx.Client.__init__`` so monkey-patches don't leak."""
    saved_client = httpx.Client.__init__
    saved_async = httpx.AsyncClient.__init__
    yield None
    httpx.Client.__init__ = saved_client  # type: ignore[method-assign]
    httpx.AsyncClient.__init__ = saved_async  # type: ignore[method-assign]


def test_resolve_tls_returns_none_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLM_CERT_FILE_ENV, raising=False)
    monkeypatch.delenv(CLM_KEY_FILE_ENV, raising=False)
    monkeypatch.delenv(CLM_CA_FILE_ENV, raising=False)
    assert clm_tls_launcher._resolve_tls_from_env() is None


def test_resolve_tls_builds_config_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = _make_pki(tmp_path)
    monkeypatch.setenv(CLM_CERT_FILE_ENV, str(pki["cert"]))
    monkeypatch.setenv(CLM_KEY_FILE_ENV, str(pki["key"]))
    monkeypatch.setenv(CLM_CA_FILE_ENV, str(pki["ca"]))
    cfg = clm_tls_launcher._resolve_tls_from_env()
    assert cfg is not None
    assert cfg.verify_mode == "required"
    assert cfg.ca_file == pki["ca"]


def test_install_httpx_mtls_defaults_injects_verify_kwarg(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """A bare ``httpx.Client()`` after the patch picks up the SSLContext."""
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="required",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.Client.__init__

    def _capture(self: httpx.Client, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        # Don't actually open sockets - abort here once we've recorded
        # what the patched __init__ chose to forward.
        raise RuntimeError("captured")

    httpx.Client.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.Client()
    finally:
        httpx.Client.__init__ = saved_init  # type: ignore[method-assign]

    # The patch must have inserted ``verify=`` because we didn't supply
    # one ourselves. It's an SSLContext (not a path / bool), per the
    # cluster_tls implementation note pinned by PR #1019.
    assert "verify" in captured
    import ssl

    assert isinstance(captured["verify"], ssl.SSLContext)


def test_install_httpx_mtls_defaults_honours_explicit_verify(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """If the caller already passed ``verify=`` we keep their choice - no silent override."""
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="required",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.Client.__init__

    def _capture(self: httpx.Client, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise RuntimeError("captured")

    httpx.Client.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.Client(verify=False)
    finally:
        httpx.Client.__init__ = saved_init  # type: ignore[method-assign]

    assert captured["verify"] is False


def test_run_aider_refuses_non_aider_target(capsys: pytest.CaptureFixture[str]) -> None:
    """Defensive guard: launcher only ever fronts aider; refuse anything else."""
    rc = clm_tls_launcher._run_aider(["sh", "-c", "rm -rf /"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "only supports aider" in err


def test_run_aider_no_argv_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = clm_tls_launcher._run_aider([])
    assert rc == 2
    assert "no command supplied" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Edge / failure-mode coverage (verify/clm-adapter-edges)
# ---------------------------------------------------------------------------


def test_install_httpx_mtls_defaults_also_patches_async_client(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """The ``AsyncClient`` patch is symmetric with the sync one.

    The OpenAI SDK uses ``httpx.AsyncClient`` for its async paths; if
    only ``httpx.Client`` were patched, async callers would skip the
    customer cert and fall back to the system CA bundle - silent mTLS
    bypass.  This pins the async monkey-patch.
    """
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="required",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.AsyncClient.__init__

    def _capture(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise RuntimeError("captured")

    httpx.AsyncClient.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.AsyncClient()
    finally:
        httpx.AsyncClient.__init__ = saved_init  # type: ignore[method-assign]

    import ssl

    assert "verify" in captured
    assert isinstance(captured["verify"], ssl.SSLContext)


def test_install_httpx_mtls_defaults_async_client_honours_explicit_verify(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """When the caller explicitly sets ``verify=`` on AsyncClient, the patch must not override."""
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="required",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.AsyncClient.__init__

    def _capture(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise RuntimeError("captured")

    httpx.AsyncClient.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.AsyncClient(verify=False)
    finally:
        httpx.AsyncClient.__init__ = saved_init  # type: ignore[method-assign]

    assert captured["verify"] is False


def test_install_httpx_mtls_defaults_required_mode_keeps_hostname_check(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """In ``required`` mode the SSLContext must enforce hostname verification.

    A frequent mTLS regression is silently disabling hostname checks in
    pursuit of a "just make it work" patch - that turns full mTLS into
    transport encryption only and lets a leaf cert be lifted onto a
    different gateway DNS name.  This test pins ``check_hostname=True``
    on the context the launcher injects.
    """
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="required",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.Client.__init__

    def _capture(self: httpx.Client, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise RuntimeError("captured")

    httpx.Client.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.Client()
    finally:
        httpx.Client.__init__ = saved_init  # type: ignore[method-assign]

    import ssl

    ctx = captured["verify"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_install_httpx_mtls_defaults_disabled_mode_drops_hostname_check(
    tmp_path: Path,
    httpx_init_restored: object,
) -> None:
    """In ``disabled`` mode hostname check is intentionally off (operator opt-in).

    The launcher should respect the operator's chosen verify_mode; if
    they requested ``disabled`` (e.g. for a staged rollout against a
    self-signed gateway) the SSLContext must reflect that without the
    launcher silently re-enabling it.
    """
    pki = _make_pki(tmp_path)
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["cert"],
        key_file=pki["key"],
        verify_mode="disabled",
    )
    captured: dict[str, object] = {}
    saved_init = httpx.Client.__init__

    def _capture(self: httpx.Client, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise RuntimeError("captured")

    httpx.Client.__init__ = _capture  # type: ignore[method-assign]
    try:
        clm_tls_launcher.install_httpx_mtls_defaults(cfg)
        with pytest.raises(RuntimeError, match="captured"):
            httpx.Client()
    finally:
        httpx.Client.__init__ = saved_init  # type: ignore[method-assign]

    import ssl

    ctx = captured["verify"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_run_aider_string_systemexit_code_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A string SystemExit code (rare but documented in CPython) maps to rc=1.

    ``runpy.run_module`` propagates whatever ``sys.exit(...)`` was called
    with; CPython allows strings ("error message") which print to stderr
    and exit with rc=1.  The launcher's exception handler must collapse
    these to a non-zero integer rc rather than returning the string
    upstream (where Popen would mishandle it).
    """

    def _fake_run_module(name: str, **kwargs: object) -> None:
        raise SystemExit("custom-error-message")

    monkeypatch.setattr(clm_tls_launcher.runpy, "run_module", _fake_run_module)
    rc = clm_tls_launcher._run_aider(["aider", "--message", "x"])
    assert rc == 1


def test_run_aider_none_systemexit_code_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """``sys.exit(None)`` (success) maps to rc=0."""

    def _fake_run_module(name: str, **kwargs: object) -> None:
        raise SystemExit(None)

    monkeypatch.setattr(clm_tls_launcher.runpy, "run_module", _fake_run_module)
    rc = clm_tls_launcher._run_aider(["aider"])
    assert rc == 0


def test_run_aider_int_systemexit_code_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aider's own non-zero exit code is forwarded verbatim."""

    def _fake_run_module(name: str, **kwargs: object) -> None:
        raise SystemExit(42)

    monkeypatch.setattr(clm_tls_launcher.runpy, "run_module", _fake_run_module)
    rc = clm_tls_launcher._run_aider(["aider", "--message", "x"])
    assert rc == 42


def test_main_with_no_tls_env_skips_monkey_patches(
    monkeypatch: pytest.MonkeyPatch,
    httpx_init_restored: object,
) -> None:
    """When mTLS env triple is absent, ``main`` does not install monkey-patches.

    Bare-HTTPS spawns must not see the launcher's defaults injected,
    otherwise non-mTLS deployments inherit a misconfigured SSLContext.
    """
    monkeypatch.delenv(CLM_CERT_FILE_ENV, raising=False)
    monkeypatch.delenv(CLM_KEY_FILE_ENV, raising=False)
    monkeypatch.delenv(CLM_CA_FILE_ENV, raising=False)

    saved_client = httpx.Client.__init__
    saved_async = httpx.AsyncClient.__init__

    def _stub_run(_argv: list[str]) -> int:
        return 0

    monkeypatch.setattr(clm_tls_launcher, "_run_aider", _stub_run)
    rc = clm_tls_launcher.main(["aider"])
    assert rc == 0
    # The init slots must be byte-identical to before main() ran.
    assert httpx.Client.__init__ is saved_client
    assert httpx.AsyncClient.__init__ is saved_async


def test_resolve_tls_falls_back_to_required_for_bogus_verify_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launcher-side: an invalid ``CLM_VERIFY_MODE`` defensively falls back to ``required``.

    The launcher runs *after* the adapter has already validated the env
    triple; reaching ``_resolve_tls_from_env`` with a bogus value would
    indicate operator tampering or env corruption.  The defensive
    behaviour is to refuse to silently relax to anything other than
    ``required`` - fail-safe, not fail-open.
    """
    pki = _make_pki(tmp_path)
    monkeypatch.setenv(CLM_CERT_FILE_ENV, str(pki["cert"]))
    monkeypatch.setenv(CLM_KEY_FILE_ENV, str(pki["key"]))
    monkeypatch.setenv(CLM_CA_FILE_ENV, str(pki["ca"]))
    monkeypatch.setenv("CLM_VERIFY_MODE", "trust-me-bro")
    cfg = clm_tls_launcher._resolve_tls_from_env()
    assert cfg is not None
    assert cfg.verify_mode == "required"
