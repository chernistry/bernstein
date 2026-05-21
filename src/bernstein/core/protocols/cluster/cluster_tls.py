"""Native mTLS for cluster node-to-node transport.

The cluster transport historically rides over plain HTTP. The JWT bearer
token authenticates the *caller*, but the channel itself is unencrypted -
anyone on the path sees the token and the task payload. For internal-only
deployments that's acceptable; for any internet-facing or regulated
deployment it is not.

This module provides the building blocks for opt-in mTLS:

- :class:`TLSConfig` - dataclass capturing CA bundle, server cert/key,
  and client verification mode.
- :func:`build_ssl_context` - assembles an :class:`ssl.SSLContext` for the
  server side (FastAPI / uvicorn) that loads the server cert chain and
  enforces the configured client-cert verification mode.
- :func:`build_httpx_client_kwargs` - produces a kwargs dict ready to splat
  into :class:`httpx.Client` for the worker side, enabling mutual auth
  against the central server.

Phase 1 deliberately keeps this small: no rotation automation, no ACME, no
per-tenant CA isolation. Operators bring their own CA (or use the
``bernstein cluster bootstrap-ca`` helper for self-hosted internal clusters)
and wire the artifacts in via :class:`ClusterConfig.tls`.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

VerifyMode = Literal["required", "optional", "disabled"]

_VALID_MODES: tuple[VerifyMode, ...] = ("required", "optional", "disabled")

# TLS 1.2 cipher allowlist - modern AEAD suites with forward secrecy only.
# Drops PSK (we don't pre-share keys), pure-RSA key exchange (no PFS), and
# every legacy weak family (NULL/EXPORT/RC4/DES/MD5/IDEA/SEED/RC2/aNULL).
# OpenSSL's default cipher list on Linux ships PSK suites, so an explicit
# allowlist is the simplest way to keep the surface auditable across
# distros and Python builds (Py 3.12 ubuntu was failing the weak-suite
# audit on the unconstrained default).
# TLS 1.3 cipher selection is independent - its three default suites
# (TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256,
# TLS_AES_128_GCM_SHA256) are always permitted and use AEAD + PFS by design.
_CIPHER_ALLOWLIST = "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20"


class TLSConfigError(ValueError):
    """Raised when a :class:`TLSConfig` is malformed or references missing files."""


@dataclass(frozen=True)
class TLSConfig:
    """Configuration for mTLS on cluster node-to-node transport.

    Attributes:
        ca_file: Path to a PEM-encoded CA bundle used to verify the peer.
            Required unless ``verify_mode`` is ``"disabled"``.
        cert_file: Path to the local PEM-encoded certificate (server cert
            on the central node, client cert on a worker).
        key_file: Path to the matching PEM-encoded private key. Should be
            mode 0600 - the bootstrap helper sets this automatically.
        verify_mode: Peer-cert verification policy. ``"required"`` enforces
            full mTLS; ``"optional"`` requests but does not require a peer
            cert (useful for staged rollouts); ``"disabled"`` accepts any
            connection (still TLS, but no client-cert verification).
    """

    ca_file: Path
    cert_file: Path
    key_file: Path
    verify_mode: VerifyMode = "required"

    def __post_init__(self) -> None:
        if self.verify_mode not in _VALID_MODES:
            raise TLSConfigError(f"verify_mode must be one of {_VALID_MODES}, got {self.verify_mode!r}")
        if not isinstance(self.ca_file, Path):
            raise TLSConfigError("ca_file must be a pathlib.Path")
        if not isinstance(self.cert_file, Path):
            raise TLSConfigError("cert_file must be a pathlib.Path")
        if not isinstance(self.key_file, Path):
            raise TLSConfigError("key_file must be a pathlib.Path")

    def validate_paths(self) -> None:
        """Check that all referenced cert/key/CA files exist on disk.

        Raises:
            TLSConfigError: If any path is missing, with a message naming
                the field and resolved path so an operator can fix it.
        """
        missing: list[str] = []
        if self.verify_mode != "disabled" and not _resolve(self.ca_file).is_file():
            missing.append(f"ca_file={self.ca_file}")
        if not _resolve(self.cert_file).is_file():
            missing.append(f"cert_file={self.cert_file}")
        if not _resolve(self.key_file).is_file():
            missing.append(f"key_file={self.key_file}")
        if missing:
            raise TLSConfigError("TLSConfig references missing files: " + ", ".join(missing))


def _resolve(path: Path) -> Path:
    """Expand ``~`` and resolve to absolute, but tolerate non-existent paths."""
    return path.expanduser().resolve(strict=False)


def build_ssl_context(cfg: TLSConfig) -> ssl.SSLContext:
    """Build a server-side :class:`ssl.SSLContext` from a :class:`TLSConfig`.

    The resulting context loads the local cert chain and applies the
    configured client-cert verification mode. Suitable for passing to
    uvicorn via ``ssl=`` or to any ASGI server that accepts an SSLContext.

    Args:
        cfg: The TLS configuration. Paths are validated before context
            construction so the operator gets a fast, readable error.

    Returns:
        A configured :class:`ssl.SSLContext`.

    Raises:
        TLSConfigError: If referenced files are missing or unreadable.
    """
    cfg.validate_paths()
    ctx = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
    # Pin TLS 1.2 floor explicitly. ssl.create_default_context() leaves
    # minimum_version at TLSVersion.MINIMUM_SUPPORTED (a sentinel that
    # defers to OpenSSL's system policy) on some Python builds - that
    # sentinel sorts numerically below TLSVersion.TLSv1_2 (771), so a
    # downstream `>= TLSv1_2` security assertion would silently fail.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(_CIPHER_ALLOWLIST)
    ctx.load_cert_chain(certfile=str(_resolve(cfg.cert_file)), keyfile=str(_resolve(cfg.key_file)))

    if cfg.verify_mode == "disabled":
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ctx.load_verify_locations(cafile=str(_resolve(cfg.ca_file)))
    if cfg.verify_mode == "required":
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.verify_mode = ssl.CERT_OPTIONAL
    return ctx


def build_httpx_client_kwargs(cfg: TLSConfig | None) -> dict[str, Any]:
    """Build kwargs for :class:`httpx.Client` from a :class:`TLSConfig`.

    Returns a kwargs dict ready to splat into ``httpx.Client(...)``. When
    ``cfg`` is ``None``, returns an empty dict so callers can apply the
    result unconditionally.

    The returned ``verify=`` is an :class:`ssl.SSLContext` (or ``False``
    for ``verify_mode="disabled"``) - httpx 0.28+ deprecated string
    paths for ``verify``, and constructing the context here also pre-loads
    the client cert chain in one place.

    Args:
        cfg: The TLS configuration, or ``None`` for plain HTTP.

    Returns:
        ``{"verify": <SSLContext|False>}`` when TLS is on; ``{}`` when
        ``cfg`` is ``None``.

    Raises:
        TLSConfigError: If ``cfg`` is provided but referenced files are
            missing or unreadable.
    """
    if cfg is None:
        return {}
    cfg.validate_paths()
    if cfg.verify_mode == "disabled":
        # Opt-in plaintext-verify path: only when operator sets verify_mode="disabled" (default is "required").
        ctx = ssl.create_default_context()  # NOSONAR python:S5527 - opt-in verify_mode="disabled" only.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers(_CIPHER_ALLOWLIST)
        ctx.check_hostname = False  # NOSONAR python:S5527 - hostname check off only on opt-in disabled mode.
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR python:S4830 - peer-cert check skipped only on opt-in disabled mode.
        ctx.load_cert_chain(certfile=str(_resolve(cfg.cert_file)), keyfile=str(_resolve(cfg.key_file)))
        return {"verify": ctx}
    # Secure path (default/optional): create_default_context keeps check_hostname=True and verifies cfg.ca_file.
    ctx = ssl.create_default_context(cafile=str(_resolve(cfg.ca_file)))  # NOSONAR python:S5527
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(_CIPHER_ALLOWLIST)
    ctx.load_cert_chain(certfile=str(_resolve(cfg.cert_file)), keyfile=str(_resolve(cfg.key_file)))
    return {"verify": ctx}


__all__ = [
    "TLSConfig",
    "TLSConfigError",
    "VerifyMode",
    "build_httpx_client_kwargs",
    "build_ssl_context",
]
