"""Phase 1 mTLS hardening tests for cluster node-to-node transport.

These tests complement ``test_cluster_tls.py`` by covering the security
properties of the SSLContext objects that
:func:`bernstein.core.protocols.cluster.cluster_tls.build_ssl_context` and
:func:`build_httpx_client_kwargs` hand to uvicorn / httpx — the parts an
operator cannot override after the fact.

Specifically:

* hostname verification is on by default for clients and only flipped off
  when ``verify_mode='disabled'`` is set explicitly;
* TLS 1.2 is the floor on both sides;
* the cipher list excludes NULL / EXPORT / RC4 / DES / MD5 / anonymous /
  PSK suites and pure-RSA key exchange (i.e. no forward secrecy);
* the dataclass refuses obviously broken configs (verify_mode != 'disabled'
  must still ship a CA, paths must be ``Path`` instances);
* a real TLS handshake performed in-process between two
  :class:`ssl.MemoryBIO` halves accepts a peer cert chained to the
  configured CA and rejects an alien cert / an expired cert / a client
  presenting no cert when ``verify_mode='required'``.

The handshake helper uses ``MemoryBIO`` so the tests are deterministic and
do not need a subprocess, free port, or wall-clock waits.
"""

from __future__ import annotations

import contextlib
import datetime
import ssl
from pathlib import Path

import pytest
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

# ---------------------------------------------------------------------------
# PKI helpers
# ---------------------------------------------------------------------------


def _build_ca(now: datetime.datetime, cn: str = "phase1-ca") -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _build_leaf(
    *,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    cn: str,
    san_dns: list[str],
    is_server: bool,
    not_before: datetime.datetime,
    not_after: datetime.datetime,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    eku_oid = x509.ExtendedKeyUsageOID.SERVER_AUTH if is_server else x509.ExtendedKeyUsageOID.CLIENT_AUTH
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku_oid]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(d) for d in san_dns]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf, leaf_key


def _write_pem(out: Path, name: str, cert: x509.Certificate, key: rsa.RSAPrivateKey) -> tuple[Path, Path]:
    cert_path = out / f"{name}.crt"
    key_path = out / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _write_ca(out: Path, cert: x509.Certificate) -> Path:
    p = out / "ca.crt"
    p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return p


@pytest.fixture
def pki(tmp_path: Path) -> dict[str, Path]:
    """A complete CA + server + client cert/key set living entirely on disk."""
    now = datetime.datetime.now(datetime.UTC)
    ca_cert, ca_key = _build_ca(now)
    server_cert, server_key = _build_leaf(
        ca_cert=ca_cert,
        ca_key=ca_key,
        cn="phase1-server",
        san_dns=["localhost", "phase1-server"],
        is_server=True,
        not_before=now - datetime.timedelta(minutes=5),
        not_after=now + datetime.timedelta(days=1),
    )
    client_cert, client_key = _build_leaf(
        ca_cert=ca_cert,
        ca_key=ca_key,
        cn="phase1-worker",
        san_dns=["phase1-worker"],
        is_server=False,
        not_before=now - datetime.timedelta(minutes=5),
        not_after=now + datetime.timedelta(days=1),
    )
    ca_path = _write_ca(tmp_path, ca_cert)
    server_cert_path, server_key_path = _write_pem(tmp_path, "server", server_cert, server_key)
    client_cert_path, client_key_path = _write_pem(tmp_path, "client", client_cert, client_key)
    return {
        "ca": ca_path,
        "server_cert": server_cert_path,
        "server_key": server_key_path,
        "client_cert": client_cert_path,
        "client_key": client_key_path,
    }


# ---------------------------------------------------------------------------
# In-process MemoryBIO handshake — deterministic, no sockets, no subprocess.
# ---------------------------------------------------------------------------


def _do_handshake(
    server_ctx: ssl.SSLContext,
    client_ctx: ssl.SSLContext,
    server_hostname: str = "localhost",
) -> None:
    """Drive a TLS handshake between two ``MemoryBIO`` pairs until both sides finish.

    Raises whatever ``ssl.SSLError`` either side emits — letting tests assert
    on the precise failure mode.
    """
    s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    c_in, c_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_obj = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    client_obj = client_ctx.wrap_bio(c_in, c_out, server_side=False, server_hostname=server_hostname)

    for _ in range(64):  # bounded — the handshake completes well within this
        for obj, peer_in, peer_out in (
            (client_obj, s_in, c_out),
            (server_obj, c_in, s_out),
        ):
            with contextlib.suppress(ssl.SSLWantReadError, ssl.SSLWantWriteError):
                obj.do_handshake()
            data = peer_out.read()
            if data:
                peer_in.write(data)
        if _all_done(client_obj) and _all_done(server_obj):
            return
    raise AssertionError("TLS handshake did not converge")


def _all_done(obj: ssl.SSLObject) -> bool:
    try:
        obj.do_handshake()
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
        return False
    else:
        return True


def _client_ctx_from_kwargs(kwargs: dict[str, object]) -> ssl.SSLContext:
    verify = kwargs["verify"]
    assert isinstance(verify, ssl.SSLContext)
    return verify


# ---------------------------------------------------------------------------
# Defaults & static security properties
# ---------------------------------------------------------------------------


def test_server_context_enforces_tls12_minimum(pki: dict[str, Path]) -> None:
    cfg = TLSConfig(ca_file=pki["ca"], cert_file=pki["server_cert"], key_file=pki["server_key"])
    ctx = build_ssl_context(cfg)
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_client_context_enforces_tls12_minimum(pki: dict[str, Path]) -> None:
    cfg = TLSConfig(ca_file=pki["ca"], cert_file=pki["client_cert"], key_file=pki["client_key"])
    ctx = _client_ctx_from_kwargs(build_httpx_client_kwargs(cfg))
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_client_context_keeps_hostname_verification_on(pki: dict[str, Path]) -> None:
    cfg = TLSConfig(ca_file=pki["ca"], cert_file=pki["client_cert"], key_file=pki["client_key"])
    ctx = _client_ctx_from_kwargs(build_httpx_client_kwargs(cfg))
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_disabled_mode_drops_hostname_verification(pki: dict[str, Path]) -> None:
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["client_cert"],
        key_file=pki["client_key"],
        verify_mode="disabled",
    )
    ctx = _client_ctx_from_kwargs(build_httpx_client_kwargs(cfg))
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_optional_mode_keeps_client_side_full_verification(pki: dict[str, Path]) -> None:
    """``verify_mode='optional'`` is a server-side concept.

    The httpx client we build for a worker must still verify the server
    fully — otherwise an attacker on the path could MitM the upload.
    """
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["client_cert"],
        key_file=pki["client_key"],
        verify_mode="optional",
    )
    ctx = _client_ctx_from_kwargs(build_httpx_client_kwargs(cfg))
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_cipher_suites_exclude_known_weak(pki: dict[str, Path]) -> None:
    """No NULL/EXPORT/RC4/DES/MD5/anon/PSK suites in the negotiated list.

    Pure-RSA key exchange (no DHE/ECDHE prefix) is also excluded — without
    forward secrecy a captured handshake decrypts all past traffic if the
    server key leaks later.
    """
    cfg = TLSConfig(ca_file=pki["ca"], cert_file=pki["server_cert"], key_file=pki["server_key"])
    ctx = build_ssl_context(cfg)
    forbidden = ("NULL", "EXPORT", "RC4", "_DES_", "MD5", "anon", "PSK", "IDEA", "SEED", "RC2")
    bad: list[str] = []
    rsa_kx: list[str] = []
    for entry in ctx.get_ciphers():
        name: str = entry["name"]
        if any(token in name for token in forbidden):
            bad.append(name)
        # TLS 1.2 cipher names: e.g. "ECDHE-RSA-AES256-GCM-SHA384". A name
        # starting with a bulk-cipher token (AES/ARIA/CAMELLIA) but missing
        # both DHE and ECDHE implies pure RSA key exchange. TLS 1.3 names
        # start with "TLS_" and are exempt.
        starts_with_bulk = name.startswith(("AES", "ARIA", "CAMELLIA"))
        if starts_with_bulk and "DHE" not in name and "ECDHE" not in name:
            rsa_kx.append(name)
    assert bad == [], f"weak suites in default cipher list: {bad}"
    assert rsa_kx == [], f"non-PFS suites in default cipher list: {rsa_kx}"


def test_server_context_loads_trusted_ca_in_required_mode(pki: dict[str, Path]) -> None:
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["server_cert"],
        key_file=pki["server_key"],
        verify_mode="required",
    )
    ctx = build_ssl_context(cfg)
    stats = ctx.cert_store_stats()
    assert stats["x509"] >= 1, f"server ctx has no trusted certs loaded: {stats}"


def test_disabled_mode_does_not_require_ca_file(pki: dict[str, Path], tmp_path: Path) -> None:
    """``verify_mode='disabled'`` must not require/load a CA file."""
    cfg = TLSConfig(
        ca_file=tmp_path / "missing-ca.crt",
        cert_file=pki["server_cert"],
        key_file=pki["server_key"],
        verify_mode="disabled",
    )
    ctx = build_ssl_context(cfg)
    assert ctx.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Real handshake — accept / reject decisions
# ---------------------------------------------------------------------------


def _server_ctx_required(pki: dict[str, Path]) -> ssl.SSLContext:
    return build_ssl_context(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=pki["server_cert"],
            key_file=pki["server_key"],
            verify_mode="required",
        )
    )


def test_handshake_accepts_valid_client_cert(pki: dict[str, Path]) -> None:
    server_ctx = _server_ctx_required(pki)
    client_kwargs = build_httpx_client_kwargs(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=pki["client_cert"],
            key_file=pki["client_key"],
        )
    )
    client_ctx = _client_ctx_from_kwargs(client_kwargs)
    _do_handshake(server_ctx, client_ctx)


def test_handshake_rejects_client_with_no_cert_when_required(pki: dict[str, Path]) -> None:
    server_ctx = _server_ctx_required(pki)
    # Plain client context — trusts the CA but presents no client cert.
    client_ctx = ssl.create_default_context(cafile=str(pki["ca"]))
    with pytest.raises(ssl.SSLError):
        _do_handshake(server_ctx, client_ctx)


def test_handshake_rejects_client_signed_by_alien_ca(pki: dict[str, Path], tmp_path: Path) -> None:
    """Cert chain validation — leaf signed by a CA the server does not trust."""
    now = datetime.datetime.now(datetime.UTC)
    alien_ca_cert, alien_ca_key = _build_ca(now, cn="alien-ca")
    alien_leaf, alien_leaf_key = _build_leaf(
        ca_cert=alien_ca_cert,
        ca_key=alien_ca_key,
        cn="alien-worker",
        san_dns=["alien-worker"],
        is_server=False,
        not_before=now - datetime.timedelta(minutes=5),
        not_after=now + datetime.timedelta(days=1),
    )
    alien_dir = tmp_path / "alien"
    alien_dir.mkdir()
    cert_path, key_path = _write_pem(alien_dir, "client", alien_leaf, alien_leaf_key)

    server_ctx = _server_ctx_required(pki)
    client_kwargs = build_httpx_client_kwargs(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=cert_path,
            key_file=key_path,
            verify_mode="required",
        )
    )
    client_ctx = _client_ctx_from_kwargs(client_kwargs)
    with pytest.raises(ssl.SSLError):
        _do_handshake(server_ctx, client_ctx)


def test_handshake_rejects_expired_client_cert(pki: dict[str, Path], tmp_path: Path) -> None:
    """Expired client cert — Phase 1 must reject, not silently accept."""
    now = datetime.datetime.now(datetime.UTC)
    expired_dir = tmp_path / "expired"
    expired_dir.mkdir()
    fresh_ca_cert, fresh_ca_key = _build_ca(now, cn="expired-ca")
    expired_leaf, expired_leaf_key = _build_leaf(
        ca_cert=fresh_ca_cert,
        ca_key=fresh_ca_key,
        cn="expired-worker",
        san_dns=["expired-worker"],
        is_server=False,
        not_before=now - datetime.timedelta(days=10),
        not_after=now - datetime.timedelta(days=1),
    )
    leaf_cert_path, leaf_key_path = _write_pem(expired_dir, "client", expired_leaf, expired_leaf_key)
    fresh_ca_path = _write_ca(expired_dir, fresh_ca_cert)

    # Server trusts the new CA so the only ground for rejection is expiry.
    server_ctx = build_ssl_context(
        TLSConfig(
            ca_file=fresh_ca_path,
            cert_file=pki["server_cert"],
            key_file=pki["server_key"],
            verify_mode="required",
        )
    )
    # Client trusts the *real* server CA so the server hello validates first.
    client_kwargs = build_httpx_client_kwargs(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=leaf_cert_path,
            key_file=leaf_key_path,
            verify_mode="required",
        )
    )
    client_ctx = _client_ctx_from_kwargs(client_kwargs)
    with pytest.raises(ssl.SSLError):
        _do_handshake(server_ctx, client_ctx)


def test_handshake_optional_mode_accepts_client_without_cert(pki: dict[str, Path]) -> None:
    """``verify_mode='optional'`` lets a client skip the cert during a staged rollout."""
    server_ctx = build_ssl_context(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=pki["server_cert"],
            key_file=pki["server_key"],
            verify_mode="optional",
        )
    )
    client_ctx = ssl.create_default_context(cafile=str(pki["ca"]))
    # Should NOT raise — handshake completes without a client cert.
    _do_handshake(server_ctx, client_ctx)


def test_client_rejects_server_without_matching_san(pki: dict[str, Path]) -> None:
    """Hostname mismatch — client must refuse a cert whose SAN does not include the host."""
    server_ctx = _server_ctx_required(pki)
    client_kwargs = build_httpx_client_kwargs(
        TLSConfig(
            ca_file=pki["ca"],
            cert_file=pki["client_cert"],
            key_file=pki["client_key"],
        )
    )
    client_ctx = _client_ctx_from_kwargs(client_kwargs)
    # Real server SAN is ['localhost', 'phase1-server']; a connection to a
    # different host name must be rejected by the client.
    with pytest.raises(ssl.SSLError):
        _do_handshake(server_ctx, client_ctx, server_hostname="evil.example.com")


# ---------------------------------------------------------------------------
# Static config-shape checks the existing suite missed
# ---------------------------------------------------------------------------


def test_required_mode_validates_ca_path(pki: dict[str, Path], tmp_path: Path) -> None:
    """``verify_mode='required'`` without a real CA file must fail fast."""
    cfg = TLSConfig(
        ca_file=tmp_path / "ghost-ca.crt",
        cert_file=pki["server_cert"],
        key_file=pki["server_key"],
        verify_mode="required",
    )
    with pytest.raises(TLSConfigError, match="ca_file"):
        build_ssl_context(cfg)


def test_disabled_client_still_loads_local_cert(pki: dict[str, Path]) -> None:
    """In 'disabled' mode the client cert/key must still be loaded.

    The httpx-side helper still calls ``load_cert_chain`` so the client can
    present its own cert if a downstream peer happens to request one. That
    keeps 'disabled' a *peer-verification* opt-out, not an own-cert opt-out.
    """
    cfg = TLSConfig(
        ca_file=pki["ca"],
        cert_file=pki["client_cert"],
        key_file=pki["client_key"],
        verify_mode="disabled",
    )
    ctx = _client_ctx_from_kwargs(build_httpx_client_kwargs(cfg))
    assert ctx.verify_mode == ssl.CERT_NONE
    # A real handshake against a server demanding client auth confirms the
    # cert was loaded — without it, this would fail at the server side.
    server_ctx = _server_ctx_required(pki)
    _do_handshake(server_ctx, ctx)
