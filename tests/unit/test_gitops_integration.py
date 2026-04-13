"""Tests for GitHub issue #636: ArgoCD/Flux GitOps integration."""

from __future__ import annotations

from typing import Any

import pytest

from bernstein.core.protocols.gitops_integration import (
    GitOpsClient,
    GitOpsProvider,
    HealthCheck,
    SyncRequest,
    SyncStatus,
    render_deployment_report,
    wait_for_sync,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _argocd_request(
    *,
    app: str = "my-app",
    revision: str = "abc123",
    namespace: str = "default",
    server_url: str | None = None,
    auth_token_env: str = "ARGOCD_TOKEN",
) -> SyncRequest:
    return SyncRequest(
        provider=GitOpsProvider.ARGOCD,
        app_name=app,
        revision=revision,
        namespace=namespace,
        server_url=server_url,
        auth_token_env=auth_token_env,
    )


def _flux_request(
    *,
    app: str = "my-app",
    revision: str = "def456",
    namespace: str = "flux-system",
    server_url: str | None = None,
    auth_token_env: str = "FLUX_TOKEN",
) -> SyncRequest:
    return SyncRequest(
        provider=GitOpsProvider.FLUX,
        app_name=app,
        revision=revision,
        namespace=namespace,
        server_url=server_url,
        auth_token_env=auth_token_env,
    )


class _FakeClock:
    """Injectable clock for testing wait_for_sync."""

    def __init__(self, times: list[float]) -> None:
        self._times = list(times)
        self._idx = 0

    def time(self) -> float:
        t = self._times[min(self._idx, len(self._times) - 1)]
        self._idx += 1
        return t


# ---------------------------------------------------------------------------
# GitOpsProvider enum
# ---------------------------------------------------------------------------


class TestGitOpsProvider:
    def test_argocd_value(self) -> None:
        assert GitOpsProvider.ARGOCD == "argocd"

    def test_flux_value(self) -> None:
        assert GitOpsProvider.FLUX == "flux"

    def test_generic_value(self) -> None:
        assert GitOpsProvider.GENERIC == "generic"

    def test_is_str_subclass(self) -> None:
        assert isinstance(GitOpsProvider.ARGOCD, str)


# ---------------------------------------------------------------------------
# SyncRequest dataclass
# ---------------------------------------------------------------------------


class TestSyncRequest:
    def test_frozen(self) -> None:
        req = _argocd_request()
        with pytest.raises(AttributeError):
            req.app_name = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        req = SyncRequest(
            provider=GitOpsProvider.ARGOCD,
            app_name="x",
            revision="r",
            namespace="ns",
        )
        assert req.server_url is None
        assert req.auth_token_env == "GITOPS_TOKEN"

    def test_custom_server_url(self) -> None:
        req = _argocd_request(server_url="https://k8s.local:6443")
        assert req.server_url == "https://k8s.local:6443"


# ---------------------------------------------------------------------------
# SyncStatus dataclass
# ---------------------------------------------------------------------------


class TestSyncStatus:
    def test_frozen(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.FLUX,
            app_name="app",
            status="synced",
            health="healthy",
            revision="aaa",
        )
        with pytest.raises(AttributeError):
            status.status = "degraded"  # type: ignore[misc]

    def test_default_message(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="a",
            status="synced",
            health="healthy",
            revision="r",
        )
        assert status.message == ""


# ---------------------------------------------------------------------------
# HealthCheck dataclass
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_frozen(self) -> None:
        hc = HealthCheck(
            provider=GitOpsProvider.ARGOCD,
            app_name="app",
            healthy=True,
        )
        with pytest.raises(AttributeError):
            hc.healthy = False  # type: ignore[misc]

    def test_details_default(self) -> None:
        hc = HealthCheck(
            provider=GitOpsProvider.FLUX,
            app_name="app",
            healthy=False,
        )
        assert hc.details == {}

    def test_details_custom(self) -> None:
        hc = HealthCheck(
            provider=GitOpsProvider.ARGOCD,
            app_name="app",
            healthy=True,
            details={"pods": 3, "ready": 3},
        )
        assert hc.details["pods"] == 3


# ---------------------------------------------------------------------------
# GitOpsClient — trigger_sync
# ---------------------------------------------------------------------------


class TestTriggerSync:
    def test_argocd_sync_returns_url(self) -> None:
        client = GitOpsClient()
        result = client.trigger_sync(_argocd_request())
        assert "/api/v1/applications/" in result["url"]
        assert "my-app" in result["url"]

    def test_flux_sync_returns_url(self) -> None:
        client = GitOpsClient()
        result = client.trigger_sync(_flux_request())
        assert "source.toolkit.fluxcd.io" in result["url"]

    def test_generic_raises(self) -> None:
        client = GitOpsClient()
        req = SyncRequest(
            provider=GitOpsProvider.GENERIC,
            app_name="x",
            revision="r",
            namespace="ns",
        )
        with pytest.raises(ValueError, match="Sync not supported"):
            client.trigger_sync(req)

    def test_payload_contains_revision(self) -> None:
        client = GitOpsClient()
        result = client.trigger_sync(_argocd_request(revision="deadbeef"))
        assert result["payload"]["revision"] == "deadbeef"


# ---------------------------------------------------------------------------
# GitOpsClient — build_argocd_sync_payload
# ---------------------------------------------------------------------------


class TestBuildArgocdSyncPayload:
    def test_includes_revision(self) -> None:
        client = GitOpsClient()
        payload = client.build_argocd_sync_payload(_argocd_request(revision="abc"))
        assert payload["revision"] == "abc"

    def test_includes_sync_options_for_namespace(self) -> None:
        client = GitOpsClient()
        payload = client.build_argocd_sync_payload(_argocd_request(namespace="prod"))
        assert "Namespace=prod" in payload["syncOptions"]

    def test_includes_cluster_when_server_url_set(self) -> None:
        client = GitOpsClient()
        req = _argocd_request(server_url="https://k8s:6443")
        payload = client.build_argocd_sync_payload(req)
        assert payload["cluster"]["server"] == "https://k8s:6443"

    def test_no_cluster_key_without_server_url(self) -> None:
        client = GitOpsClient()
        payload = client.build_argocd_sync_payload(_argocd_request())
        assert "cluster" not in payload

    def test_prune_enabled(self) -> None:
        client = GitOpsClient()
        payload = client.build_argocd_sync_payload(_argocd_request())
        assert payload["prune"] is True


# ---------------------------------------------------------------------------
# GitOpsClient — build_flux_reconcile_payload
# ---------------------------------------------------------------------------


class TestBuildFluxReconcilePayload:
    def test_kind_is_git_repository(self) -> None:
        client = GitOpsClient()
        payload = client.build_flux_reconcile_payload(_flux_request())
        assert payload["kind"] == "GitRepository"

    def test_metadata_name_matches(self) -> None:
        client = GitOpsClient()
        payload = client.build_flux_reconcile_payload(_flux_request(app="web"))
        assert payload["metadata"]["name"] == "web"

    def test_spec_ref_commit(self) -> None:
        client = GitOpsClient()
        payload = client.build_flux_reconcile_payload(_flux_request(revision="feed"))
        assert payload["spec"]["ref"]["commit"] == "feed"

    def test_spec_url_when_server_url_set(self) -> None:
        client = GitOpsClient()
        req = _flux_request(server_url="https://git.local/repo")
        payload = client.build_flux_reconcile_payload(req)
        assert payload["spec"]["url"] == "https://git.local/repo"

    def test_no_url_without_server_url(self) -> None:
        client = GitOpsClient()
        payload = client.build_flux_reconcile_payload(_flux_request())
        assert "url" not in payload["spec"]


# ---------------------------------------------------------------------------
# GitOpsClient — check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_argocd_health_url(self) -> None:
        client = GitOpsClient()
        result = client.check_health(GitOpsProvider.ARGOCD, "my-app")
        assert "health/my-app" in result["url"]
        assert result["method"] == "GET"

    def test_flux_health_url(self) -> None:
        client = GitOpsClient()
        result = client.check_health(GitOpsProvider.FLUX, "svc")
        assert "health/svc" in result["url"]

    def test_generic_raises(self) -> None:
        client = GitOpsClient()
        with pytest.raises(ValueError, match="not supported"):
            client.check_health(GitOpsProvider.GENERIC, "app")


# ---------------------------------------------------------------------------
# GitOpsClient — get_api_url
# ---------------------------------------------------------------------------


class TestGetApiUrl:
    def test_argocd_url(self) -> None:
        client = GitOpsClient(argocd_base_url="https://argo.local")
        url = client.get_api_url(GitOpsProvider.ARGOCD, "sync/app")
        assert url == "https://argo.local/api/v1/applications/sync/app"

    def test_flux_url(self) -> None:
        client = GitOpsClient(flux_base_url="https://flux.local")
        url = client.get_api_url(GitOpsProvider.FLUX, "reconcile/x")
        assert url == "https://flux.local/apis/source.toolkit.fluxcd.io/v1/reconcile/x"

    def test_generic_raises(self) -> None:
        client = GitOpsClient()
        with pytest.raises(ValueError, match="No base URL"):
            client.get_api_url(GitOpsProvider.GENERIC, "anything")

    def test_trailing_slash_stripped(self) -> None:
        client = GitOpsClient(argocd_base_url="https://argo.local/")
        url = client.get_api_url(GitOpsProvider.ARGOCD, "ep")
        assert "argo.local//api" not in url


# ---------------------------------------------------------------------------
# GitOpsClient — get_headers
# ---------------------------------------------------------------------------


class TestGetHeaders:
    def test_authorization_header(self) -> None:
        client = GitOpsClient()
        headers = client.get_headers({"provider": GitOpsProvider.ARGOCD, "auth_token_env": "MY_T"})
        assert headers["Authorization"] == "Bearer ${MY_T}"

    def test_argocd_extra_header(self) -> None:
        client = GitOpsClient()
        headers = client.get_headers({"provider": GitOpsProvider.ARGOCD, "auth_token_env": "T"})
        assert "Argocd-Token" in headers

    def test_flux_no_extra_header(self) -> None:
        client = GitOpsClient()
        headers = client.get_headers({"provider": GitOpsProvider.FLUX, "auth_token_env": "T"})
        assert "Argocd-Token" not in headers

    def test_content_type(self) -> None:
        client = GitOpsClient()
        headers = client.get_headers({"provider": GitOpsProvider.FLUX, "auth_token_env": "T"})
        assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# wait_for_sync
# ---------------------------------------------------------------------------


class TestWaitForSync:
    def test_returns_synced_status(self) -> None:
        client = GitOpsClient()
        req = _argocd_request()
        synced = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="my-app",
            status="synced",
            health="healthy",
            revision="abc123",
        )

        def status_fn(_c: Any, _r: Any) -> SyncStatus:
            return synced

        clock = _FakeClock([0.0, 1.0])
        result = wait_for_sync(client, req, timeout_s=10, _clock=clock, _status_fn=status_fn)
        assert result.status == "synced"

    def test_timeout_returns_unknown(self) -> None:
        client = GitOpsClient()
        req = _argocd_request()
        clock = _FakeClock([0.0, 999.0])
        result = wait_for_sync(client, req, timeout_s=5, _clock=clock, _status_fn=None)
        assert result.status == "unknown"
        assert "Timed out" in result.message

    def test_polls_until_synced(self) -> None:
        client = GitOpsClient()
        req = _flux_request()
        call_count = 0

        def status_fn(_c: Any, _r: Any) -> SyncStatus | None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return SyncStatus(
                    provider=GitOpsProvider.FLUX,
                    app_name="my-app",
                    status="synced",
                    health="healthy",
                    revision="def456",
                )
            return SyncStatus(
                provider=GitOpsProvider.FLUX,
                app_name="my-app",
                status="progressing",
                health="degraded",
                revision="def456",
            )

        # Give enough time ticks for 3+ iterations.
        clock = _FakeClock([0.0, 1.0, 2.0, 3.0, 4.0])
        result = wait_for_sync(client, req, timeout_s=100, _clock=clock, _status_fn=status_fn)
        assert result.status == "synced"
        assert call_count >= 3


# ---------------------------------------------------------------------------
# render_deployment_report
# ---------------------------------------------------------------------------


class TestRenderDeploymentReport:
    def test_contains_app_name(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="cool-app",
            status="synced",
            health="healthy",
            revision="abc",
        )
        report = render_deployment_report(status)
        assert "cool-app" in report

    def test_healthy_synced_icons(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="a",
            status="synced",
            health="healthy",
            revision="r",
        )
        report = render_deployment_report(status)
        assert "[pass] synced" in report
        assert "[pass] healthy" in report

    def test_degraded_icons(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.FLUX,
            app_name="a",
            status="degraded",
            health="degraded",
            revision="r",
        )
        report = render_deployment_report(status)
        assert "[warn] degraded" in report
        assert "[fail] degraded" in report

    def test_message_included_when_present(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="a",
            status="synced",
            health="healthy",
            revision="r",
            message="All good",
        )
        report = render_deployment_report(status)
        assert "All good" in report

    def test_no_message_row_when_empty(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="a",
            status="synced",
            health="healthy",
            revision="r",
        )
        report = render_deployment_report(status)
        assert "Message" not in report

    def test_revision_in_backticks(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="a",
            status="synced",
            health="healthy",
            revision="deadbeef",
        )
        report = render_deployment_report(status)
        assert "`deadbeef`" in report

    def test_markdown_heading(self) -> None:
        status = SyncStatus(
            provider=GitOpsProvider.ARGOCD,
            app_name="web",
            status="synced",
            health="healthy",
            revision="r",
        )
        report = render_deployment_report(status)
        assert report.startswith("# Deployment Report: web")
