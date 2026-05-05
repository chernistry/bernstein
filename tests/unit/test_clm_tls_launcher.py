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
        # Don't actually open sockets — abort here once we've recorded
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
    """If the caller already passed ``verify=`` we keep their choice — no silent override."""
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
