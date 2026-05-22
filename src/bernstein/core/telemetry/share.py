"""Maintainer-share telemetry sink.

This module implements the additive RFC #1719 path. It is inert unless both
gates are true:

* ``share_with_maintainer`` resolves to true via the consent resolver.
* ``BERNSTEIN_TELEMETRY_SHARE_ENDPOINT`` is set by the runtime environment.

The request body is the same closed telemetry event JSON that the local queue
stores. Receipt data travels in HTTP headers so the maintainer-share path cannot
grow a richer event schema than the operator-audited local record.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from bernstein.core.lineage.identity import generate_keypair, sign_detached
from bernstein.core.telemetry import consent

_LOG = logging.getLogger(__name__)

SHARE_ENDPOINT_ENV: Final[str] = "BERNSTEIN_TELEMETRY_SHARE_ENDPOINT"
TIMEOUT_SECONDS: Final[float] = 3.0

_PRIVATE_KEY_NAME: Final[str] = "telemetry-share-key.pem"
_PUBLIC_KEY_NAME: Final[str] = "telemetry-share-public.pem"

_HEADER_AGENT_ID: Final[str] = "x-bernstein-telemetry-agent-id"
_HEADER_JWS: Final[str] = "x-bernstein-telemetry-jws"
_HEADER_KID: Final[str] = "x-bernstein-telemetry-kid"
_HEADER_PUBLIC_KEY: Final[str] = "x-bernstein-telemetry-public-key-pem-b64"
_HEADER_RECEIPT_VERSION: Final[str] = "x-bernstein-telemetry-receipt-version"


@dataclass(frozen=True, slots=True)
class ShareIdentity:
    """Per-install signing identity for maintainer-share receipts."""

    agent_id: str
    kid: str
    private_key_pem: str
    public_key_pem: str


def _state_dir(home: Path | None = None) -> Path:
    """Return the operator-local telemetry state directory."""
    base = home if home is not None else Path.home()
    return base / ".bernstein"


def share_private_key_path(home: Path | None = None) -> Path:
    """Return the private key path for maintainer-share receipts."""
    return _state_dir(home) / _PRIVATE_KEY_NAME


def share_public_key_path(home: Path | None = None) -> Path:
    """Return the public key path for maintainer-share receipts."""
    return _state_dir(home) / _PUBLIC_KEY_NAME


def resolve_share_endpoint(env: dict[str, str] | None = None) -> str | None:
    """Return the configured HTTPS maintainer-share endpoint, if one is set."""
    real_env = env if env is not None else os.environ
    endpoint = real_env.get(SHARE_ENDPOINT_ENV)
    if endpoint is None:
        return None
    stripped = endpoint.strip()
    if not stripped.startswith("https://"):
        return None
    return stripped


def ensure_share_identity(
    *,
    install_id: str,
    home: Path | None = None,
) -> ShareIdentity:
    """Return the local signing identity, generating it if needed.

    The private key is created only after the caller has already verified
    explicit share consent and endpoint configuration.
    """
    private_path = share_private_key_path(home)
    public_path = share_public_key_path(home)
    private_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        private_key_pem = private_path.read_text(encoding="ascii")
        public_key_pem = public_path.read_text(encoding="ascii")
    except OSError:
        private_key_pem, public_key_pem = generate_keypair()
        tmp_private = private_path.with_suffix(".tmp")
        tmp_public = public_path.with_suffix(".tmp")
        tmp_private.write_text(private_key_pem, encoding="ascii")
        tmp_public.write_text(public_key_pem, encoding="ascii")
        with contextlib.suppress(OSError):
            os.chmod(tmp_private, 0o600)
        os.replace(tmp_private, private_path)
        os.replace(tmp_public, public_path)
        with contextlib.suppress(OSError):
            os.chmod(private_path, 0o600)

    return ShareIdentity(
        agent_id=f"install:{install_id}",
        kid=f"telemetry-share:{install_id}",
        private_key_pem=private_key_pem,
        public_key_pem=public_key_pem,
    )


def emit_if_enabled(
    *,
    serialized_event: str,
    install_id: str,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    http_client: httpx.Client | None = None,
) -> bool:
    """POST a signed maintainer-share event when both gates are enabled.

    Returns ``True`` only when a send was attempted. Any error is suppressed so
    telemetry cannot break the caller.
    """
    try:
        endpoint = resolve_share_endpoint(env)
        if endpoint is None:
            return False
        if not consent.is_sharing_with_maintainer(env=env, home=home):
            return False

        identity = ensure_share_identity(install_id=install_id, home=home)
        body = serialized_event.encode("utf-8")
        jws = sign_detached(body, identity.private_key_pem, kid=identity.kid)
        public_key_b64 = base64.urlsafe_b64encode(identity.public_key_pem.encode("ascii")).rstrip(b"=").decode("ascii")
        client = http_client if http_client is not None else httpx.Client(timeout=TIMEOUT_SECONDS)
        owns_client = http_client is None
        try:
            client.post(
                endpoint,
                content=body,
                headers={
                    "content-type": "application/json",
                    _HEADER_AGENT_ID: identity.agent_id,
                    _HEADER_JWS: jws,
                    _HEADER_KID: identity.kid,
                    _HEADER_PUBLIC_KEY: public_key_b64,
                    _HEADER_RECEIPT_VERSION: "1",
                },
                timeout=TIMEOUT_SECONDS,
            )
        finally:
            if owns_client:
                client.close()
        return True
    except Exception as exc:
        _LOG.debug("telemetry share: emit failed (suppressed): %s", exc)
        return False


__all__ = [
    "SHARE_ENDPOINT_ENV",
    "ShareIdentity",
    "emit_if_enabled",
    "ensure_share_identity",
    "resolve_share_endpoint",
    "share_private_key_path",
    "share_public_key_path",
]
