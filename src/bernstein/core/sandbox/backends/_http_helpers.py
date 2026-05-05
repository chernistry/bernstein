"""Shared httpx scaffolding for cloud-REST sandbox backends.

The Blaxel, Daytona, Runloop, and Vercel backends all expose
configuration-over-REST APIs. They share the same plumbing:

1. Read credentials from environment variables; raise a typed error
   that names the missing variable when nothing is set.
2. Construct an :class:`httpx.AsyncClient` with optional mTLS material
   (via :mod:`bernstein.core.protocols.cluster.cluster_tls`) so the
   backends behave identically to the rest of the cluster transport.
3. Surface 4xx/5xx responses as a typed exception that includes the
   provider's request id for triage.

This module centralises those primitives so each provider module can
focus exclusively on the route mapping and payload shape.

This file is intentionally provider-agnostic; per-provider modules
import only the helpers they need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from bernstein.core.protocols.cluster.cluster_tls import (
    TLSConfig,
    build_httpx_client_kwargs,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class SandboxCredentialError(RuntimeError):
    """Raised when a required env var for a cloud sandbox backend is unset.

    Attributes:
        provider: Provider identifier (``"blaxel"``, ``"daytona"`` ...).
        missing: Tuple of env-var names that were not found in the
            process environment.
    """

    def __init__(self, provider: str, missing: tuple[str, ...]) -> None:
        self.provider = provider
        self.missing = missing
        names = ", ".join(missing)
        super().__init__(
            f"{provider} sandbox backend requires environment variable(s): {names}. "
            f"Set them in the orchestrator process environment before instantiating "
            f"the backend.",
        )


class SandboxApiError(RuntimeError):
    """Raised when a provider returns an HTTP error response.

    Attributes:
        provider: Provider identifier.
        status_code: HTTP status code.
        request_id: Optional provider-supplied correlation id, lifted
            from headers (X-Request-Id, X-Correlation-Id, etc.) or the
            response body.
        body: Raw response body (truncated to 1 KiB) for log inspection.
    """

    def __init__(
        self,
        provider: str,
        status_code: int,
        *,
        request_id: str | None,
        body: str,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.request_id = request_id
        self.body = body
        rid = f" request_id={request_id}" if request_id else ""
        super().__init__(
            f"{provider} API returned HTTP {status_code}{rid}: {body[:1024]}",
        )


@dataclass(frozen=True)
class HttpClientSpec:
    """Resolved spec for constructing an :class:`httpx.AsyncClient`.

    Attributes:
        base_url: Provider API root.
        headers: Default headers (Authorization, Accept, etc.) applied
            to every request.
        timeout: Per-request timeout in seconds.
        tls: Optional :class:`TLSConfig` for mTLS to a private control
            plane (corporate VPC deployments).
    """

    base_url: str
    headers: Mapping[str, str]
    timeout: float
    tls: TLSConfig | None = None


def require_env(provider: str, names: Iterable[str]) -> dict[str, str]:
    """Read required env vars; raise :class:`SandboxCredentialError` on miss.

    Args:
        provider: Provider identifier used in the error message.
        names: Required environment variable names.

    Returns:
        A dict mapping each name to its value.

    Raises:
        SandboxCredentialError: When any required name is unset or empty.
    """
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
        else:
            resolved[name] = value
    if missing:
        raise SandboxCredentialError(provider, tuple(missing))
    return resolved


def build_async_client(spec: HttpClientSpec) -> httpx.AsyncClient:
    """Construct an :class:`httpx.AsyncClient` from a :class:`HttpClientSpec`.

    Honours the cluster-shared mTLS plumbing so cloud sandbox calls can
    ride the same private CA used by the rest of the orchestrator when
    the operator wires :class:`TLSConfig` in.
    """
    tls_kwargs = build_httpx_client_kwargs(spec.tls)
    return httpx.AsyncClient(
        base_url=spec.base_url,
        headers=dict(spec.headers),
        timeout=spec.timeout,
        **tls_kwargs,
    )


def extract_request_id(response: httpx.Response) -> str | None:
    """Best-effort lookup of a provider-side correlation id."""
    for header in (
        "X-Request-Id",
        "X-Request-ID",
        "x-request-id",
        "X-Correlation-Id",
        "X-Vercel-Id",
        "Cf-Ray",
    ):
        value = response.headers.get(header)
        if value:
            return value
    try:
        payload: Any = response.json()
    except (ValueError, httpx.DecodingError):
        return None
    if isinstance(payload, dict):
        for key in ("request_id", "requestId", "id"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def raise_for_status(provider: str, response: httpx.Response) -> None:
    """Raise :class:`SandboxApiError` for any non-2xx response.

    Args:
        provider: Provider identifier injected into the error.
        response: The response to inspect.

    Raises:
        SandboxApiError: When ``response.status_code`` is >= 400.
    """
    if response.status_code < 400:
        return
    body = response.text or ""
    raise SandboxApiError(
        provider,
        response.status_code,
        request_id=extract_request_id(response),
        body=body,
    )


__all__ = [
    "HttpClientSpec",
    "SandboxApiError",
    "SandboxCredentialError",
    "build_async_client",
    "extract_request_id",
    "raise_for_status",
    "require_env",
]
