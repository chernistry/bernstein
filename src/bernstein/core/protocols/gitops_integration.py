"""GitHub issue #636: ArgoCD/Flux integration for GitOps deployment pipelines.

Builds API payloads for ArgoCD and Flux GitOps providers without calling
external APIs.  Provides a polling helper (``wait_for_sync``) and a Markdown
deployment-report renderer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_DEFAULT_ARGOCD_BASE = "https://argocd.example.com"
_DEFAULT_FLUX_BASE = "https://flux.example.com"


class GitOpsProvider(StrEnum):
    """Supported GitOps providers."""

    ARGOCD = "argocd"
    FLUX = "flux"
    GENERIC = "generic"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncRequest:
    """Request to trigger a GitOps sync.

    Attributes:
        provider: Target GitOps provider.
        app_name: Name of the application to sync.
        revision: Git revision (SHA, tag, or branch) to sync to.
        namespace: Kubernetes namespace for the application.
        server_url: Optional API server URL override.
        auth_token_env: Name of the environment variable holding the auth token.
    """

    provider: GitOpsProvider
    app_name: str
    revision: str
    namespace: str
    server_url: str | None = None
    auth_token_env: str = "GITOPS_TOKEN"


@dataclass(frozen=True)
class SyncStatus:
    """Status of a GitOps sync operation.

    Attributes:
        provider: GitOps provider that reported the status.
        app_name: Application name.
        status: Sync status string.
        health: Application health string.
        revision: Currently deployed revision.
        message: Human-readable status message.
    """

    provider: GitOpsProvider
    app_name: str
    status: str  # synced | progressing | degraded | unknown
    health: str  # healthy | degraded | missing
    revision: str
    message: str = ""


@dataclass(frozen=True)
class HealthCheck:
    """Result of a GitOps health check.

    Attributes:
        provider: GitOps provider.
        app_name: Application name.
        healthy: Whether the application is healthy.
        details: Provider-specific detail map.
    """

    provider: GitOpsProvider
    app_name: str
    healthy: bool
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GitOpsClient:
    """Builds API payloads for ArgoCD and Flux providers.

    Does **not** call external APIs — it constructs the payloads and URL
    metadata that a caller would send to the real provider.
    """

    def __init__(
        self,
        argocd_base_url: str = _DEFAULT_ARGOCD_BASE,
        flux_base_url: str = _DEFAULT_FLUX_BASE,
    ) -> None:
        self._argocd_base = argocd_base_url.rstrip("/")
        self._flux_base = flux_base_url.rstrip("/")

    # -- public API --------------------------------------------------------

    def trigger_sync(self, request: SyncRequest) -> dict[str, Any]:
        """Build a sync API payload for the given provider.

        Args:
            request: The sync request describing what to sync.

        Returns:
            A dict containing ``url``, ``headers``, and ``payload`` keys.

        Raises:
            ValueError: If the provider is not supported for sync.
        """
        if request.provider == GitOpsProvider.ARGOCD:
            payload = self.build_argocd_sync_payload(request)
        elif request.provider == GitOpsProvider.FLUX:
            payload = self.build_flux_reconcile_payload(request)
        else:
            raise ValueError(f"Sync not supported for provider {request.provider!r}")

        url = self.get_api_url(request.provider, f"sync/{request.app_name}")
        headers = self.get_headers({"provider": request.provider, "auth_token_env": request.auth_token_env})

        return {"url": url, "headers": headers, "payload": payload}

    def check_health(self, provider: GitOpsProvider, app_name: str) -> dict[str, Any]:
        """Build a health-check payload for the given provider.

        Args:
            provider: GitOps provider.
            app_name: Application name.

        Returns:
            A dict containing ``url``, ``headers``, and ``method`` keys.

        Raises:
            ValueError: If the provider is not supported for health checks.
        """
        if provider == GitOpsProvider.GENERIC:
            raise ValueError(f"Health check not supported for provider {provider!r}")

        url = self.get_api_url(provider, f"health/{app_name}")
        headers = self.get_headers({"provider": provider, "auth_token_env": "GITOPS_TOKEN"})

        return {"url": url, "headers": headers, "method": "GET"}

    def build_argocd_sync_payload(self, request: SyncRequest) -> dict[str, Any]:
        """Build an ArgoCD sync operation payload.

        Args:
            request: The sync request.

        Returns:
            ArgoCD-formatted sync payload dict.
        """
        payload: dict[str, Any] = {
            "revision": request.revision,
            "prune": True,
            "dryRun": False,
            "strategy": {"hook": {"force": False}},
            "resources": None,
        }
        if request.namespace:
            payload["syncOptions"] = [f"Namespace={request.namespace}"]
        if request.server_url:
            payload["cluster"] = {"server": request.server_url}
        return payload

    def build_flux_reconcile_payload(self, request: SyncRequest) -> dict[str, Any]:
        """Build a Flux reconciliation payload.

        Args:
            request: The sync request.

        Returns:
            Flux-formatted reconciliation payload dict.
        """
        payload: dict[str, Any] = {
            "kind": "GitRepository",
            "metadata": {
                "name": request.app_name,
                "namespace": request.namespace,
            },
            "spec": {
                "ref": {"commit": request.revision},
                "suspend": False,
            },
        }
        if request.server_url:
            payload["spec"]["url"] = request.server_url
        return payload

    def get_api_url(self, provider: GitOpsProvider, endpoint: str) -> str:
        """Construct the full API URL for a provider endpoint.

        Args:
            provider: GitOps provider.
            endpoint: Relative endpoint path (e.g. ``sync/my-app``).

        Returns:
            Full URL string.

        Raises:
            ValueError: If the provider has no known base URL.
        """
        if provider == GitOpsProvider.ARGOCD:
            return f"{self._argocd_base}/api/v1/applications/{endpoint}"
        if provider == GitOpsProvider.FLUX:
            return f"{self._flux_base}/apis/source.toolkit.fluxcd.io/v1/{endpoint}"
        raise ValueError(f"No base URL configured for provider {provider!r}")

    def get_headers(self, config: dict[str, Any]) -> dict[str, str]:
        """Build auth headers for a provider request.

        The token is referenced by environment-variable name — it is the
        caller's responsibility to resolve the env var before sending.

        Args:
            config: Dict with ``provider`` and ``auth_token_env`` keys.

        Returns:
            Headers dict with Authorization and Content-Type.
        """
        provider = config.get("provider", GitOpsProvider.GENERIC)
        token_env = str(config.get("auth_token_env", "GITOPS_TOKEN"))
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer ${{{token_env}}}",
        }
        if provider == GitOpsProvider.ARGOCD:
            headers["Argocd-Token"] = f"${{{token_env}}}"
        return headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_sync(
    client: GitOpsClient,
    request: SyncRequest,
    timeout_s: float = 300.0,
    *,
    _clock: Any = None,
    _status_fn: Any = None,
) -> SyncStatus:
    """Poll until the application is synced or the timeout expires.

    In production the caller should replace ``_status_fn`` with a real
    HTTP-based status fetcher.  By default, this returns an ``unknown``
    status after the timeout.

    Args:
        client: GitOpsClient instance (used to build the check URL).
        request: The original sync request.
        timeout_s: Maximum seconds to wait.
        _clock: Injectable time source (for testing).  Must expose
            a ``time()`` method.
        _status_fn: Callable ``(client, request) -> SyncStatus | None``
            that fetches the current status.  If ``None``, a stub is used
            that always returns ``None`` (i.e. unknown).

    Returns:
        The final SyncStatus after polling.
    """
    clock = _clock if _clock is not None else time
    deadline = clock.time() + timeout_s

    while clock.time() < deadline:
        if _status_fn is not None:
            status = _status_fn(client, request)
            if status is not None and status.status == "synced":
                logger.info("App %s synced at revision %s", request.app_name, status.revision)
                return status

    return SyncStatus(
        provider=request.provider,
        app_name=request.app_name,
        status="unknown",
        health="missing",
        revision=request.revision,
        message=f"Timed out after {timeout_s}s waiting for sync",
    )


def render_deployment_report(status: SyncStatus) -> str:
    """Render a Markdown deployment report from a SyncStatus.

    Args:
        status: The sync status to render.

    Returns:
        A Markdown-formatted string.
    """
    health_icon = "pass" if status.health == "healthy" else "fail"
    sync_icon = "pass" if status.status == "synced" else "warn"

    lines = [
        f"# Deployment Report: {status.app_name}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Provider | {status.provider} |",
        f"| Application | {status.app_name} |",
        f"| Sync Status | [{sync_icon}] {status.status} |",
        f"| Health | [{health_icon}] {status.health} |",
        f"| Revision | `{status.revision}` |",
    ]
    if status.message:
        lines.append(f"| Message | {status.message} |")
    lines.append("")
    return "\n".join(lines)
