"""Unit tests for ``bernstein.core.protocols.cluster.cluster_tls``.

Covers TLSConfig validation, file-path resolution, ssl context construction,
and the helpful error messages emitted on misconfiguration.
"""

from __future__ import annotations

import datetime
import ssl
from pathlib import Path

import pytest
from bernstein.core.models import ClusterConfig
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bernstein.core.protocols.cluster.cluster_tls import (
    TLSConfig,
    TLSConfigError,
    build_httpx_client_kwargs,
    build_ssl_context,
)


def _make_self_signed(out_dir: Path, common_name: str = "localhost") -> tuple[Path, Path, Path]:
    """Materialise a CA + server cert/key trio under ``out_dir``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    ca_path = out_dir / "ca.crt"
    cert_path = out_dir / "server.crt"
    key_path = out_dir / "server.key"
    pem_cert = cert.public_bytes(serialization.Encoding.PEM)
    ca_path.write_bytes(pem_cert)
    cert_path.write_bytes(pem_cert)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


@pytest.fixture
def cert_trio(tmp_path: Path) -> tuple[Path, Path, Path]:
    return _make_self_signed(tmp_path)


def test_invalid_verify_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(TLSConfigError, match="verify_mode"):
        TLSConfig(
            ca_file=tmp_path / "ca.crt",
            cert_file=tmp_path / "c.crt",
            key_file=tmp_path / "c.key",
            verify_mode="bogus",  # type: ignore[arg-type]
        )


def test_non_path_field_raises(tmp_path: Path) -> None:
    with pytest.raises(TLSConfigError, match="ca_file must be a pathlib.Path"):
        TLSConfig(
            ca_file="ca.crt",  # type: ignore[arg-type]
            cert_file=tmp_path / "c.crt",
            key_file=tmp_path / "c.key",
        )


def test_validate_paths_lists_missing(tmp_path: Path) -> None:
    cfg = TLSConfig(
        ca_file=tmp_path / "missing-ca.crt",
        cert_file=tmp_path / "missing-cert.crt",
        key_file=tmp_path / "missing-key.key",
    )
    with pytest.raises(TLSConfigError) as excinfo:
        cfg.validate_paths()
    msg = str(excinfo.value)
    assert "ca_file=" in msg
    assert "cert_file=" in msg
    assert "key_file=" in msg


def test_validate_paths_skips_ca_when_disabled(tmp_path: Path, cert_trio: tuple[Path, Path, Path]) -> None:
    _, cert, key = cert_trio
    cfg = TLSConfig(
        ca_file=tmp_path / "definitely-missing.crt",
        cert_file=cert,
        key_file=key,
        verify_mode="disabled",
    )
    cfg.validate_paths()


def test_build_ssl_context_required(cert_trio: tuple[Path, Path, Path]) -> None:
    ca, cert, key = cert_trio
    cfg = TLSConfig(ca_file=ca, cert_file=cert, key_file=key, verify_mode="required")
    ctx = build_ssl_context(cfg)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_build_ssl_context_optional(cert_trio: tuple[Path, Path, Path]) -> None:
    ca, cert, key = cert_trio
    cfg = TLSConfig(ca_file=ca, cert_file=cert, key_file=key, verify_mode="optional")
    ctx = build_ssl_context(cfg)
    assert ctx.verify_mode == ssl.CERT_OPTIONAL


def test_build_ssl_context_disabled_skips_ca(tmp_path: Path, cert_trio: tuple[Path, Path, Path]) -> None:
    _, cert, key = cert_trio
    cfg = TLSConfig(
        ca_file=tmp_path / "missing-ca.crt",
        cert_file=cert,
        key_file=key,
        verify_mode="disabled",
    )
    ctx = build_ssl_context(cfg)
    assert ctx.verify_mode == ssl.CERT_NONE


def test_build_ssl_context_missing_cert_raises(tmp_path: Path) -> None:
    cfg = TLSConfig(
        ca_file=tmp_path / "ca.crt",
        cert_file=tmp_path / "missing.crt",
        key_file=tmp_path / "missing.key",
    )
    with pytest.raises(TLSConfigError):
        build_ssl_context(cfg)


def test_build_httpx_client_kwargs_none_returns_empty() -> None:
    assert build_httpx_client_kwargs(None) == {}


def test_build_httpx_client_kwargs_required(cert_trio: tuple[Path, Path, Path]) -> None:
    ca, cert, key = cert_trio
    cfg = TLSConfig(ca_file=ca, cert_file=cert, key_file=key, verify_mode="required")
    kwargs = build_httpx_client_kwargs(cfg)
    assert isinstance(kwargs["verify"], ssl.SSLContext)
    assert kwargs["verify"].verify_mode == ssl.CERT_REQUIRED


def test_build_httpx_client_kwargs_disabled(cert_trio: tuple[Path, Path, Path]) -> None:
    ca, cert, key = cert_trio
    cfg = TLSConfig(ca_file=ca, cert_file=cert, key_file=key, verify_mode="disabled")
    kwargs = build_httpx_client_kwargs(cfg)
    assert isinstance(kwargs["verify"], ssl.SSLContext)
    assert kwargs["verify"].verify_mode == ssl.CERT_NONE


def test_build_httpx_client_kwargs_missing_files_raises(tmp_path: Path) -> None:
    cfg = TLSConfig(
        ca_file=tmp_path / "ca.crt",
        cert_file=tmp_path / "missing.crt",
        key_file=tmp_path / "missing.key",
    )
    with pytest.raises(TLSConfigError):
        build_httpx_client_kwargs(cfg)


def test_cluster_config_url_scheme_default() -> None:
    cfg = ClusterConfig(enabled=True)
    assert cfg.tls is None
    assert cfg.cluster_url_scheme == "http"


def test_cluster_config_url_scheme_with_tls(cert_trio: tuple[Path, Path, Path]) -> None:
    ca, cert, key = cert_trio
    tls = TLSConfig(ca_file=ca, cert_file=cert, key_file=key)
    cfg = ClusterConfig(enabled=True, tls=tls)
    assert cfg.cluster_url_scheme == "https"


def test_tilde_expansion_in_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Path.home()
    sub = home / ".bernstein" / "cluster"
    sub.mkdir(parents=True, exist_ok=True)
    ca, cert, key = _make_self_signed(sub)
    cfg = TLSConfig(
        ca_file=Path("~/.bernstein/cluster/ca.crt"),
        cert_file=Path("~/.bernstein/cluster/server.crt"),
        key_file=Path("~/.bernstein/cluster/server.key"),
    )
    ctx = build_ssl_context(cfg)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    _ = ca, cert, key
