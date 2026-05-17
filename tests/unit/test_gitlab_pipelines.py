"""Unit tests for ``bernstein.gitlab_app.pipelines``."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bernstein.gitlab_app.pipelines import (
    PIPELINE_STATUS_NAME,
    PipelineStatusClient,
    build_status_body,
    conclusion_to_state,
)


class TestConclusionToState:
    def test_known_mappings(self) -> None:
        assert conclusion_to_state("success") == "success"
        assert conclusion_to_state("failure") == "failed"
        assert conclusion_to_state("neutral") == "success"
        assert conclusion_to_state("cancelled") == "canceled"
        assert conclusion_to_state("timed_out") == "failed"

    def test_unknown_falls_back_to_failed(self) -> None:
        assert conclusion_to_state("anything-else") == "failed"

    def test_empty_falls_back_to_failed(self) -> None:
        assert conclusion_to_state("") == "failed"


class TestBuildStatusBody:
    def test_minimal(self) -> None:
        body = build_status_body("running", "hello")
        assert body["state"] == "running"
        assert body["description"] == "hello"
        assert body["name"] == PIPELINE_STATUS_NAME
        assert "target_url" not in body

    def test_with_target_and_ref(self) -> None:
        body = build_status_body("success", "x", target_url="https://x", ref="main")
        assert body["target_url"] == "https://x"
        assert body["ref"] == "main"

    def test_description_truncated(self) -> None:
        body = build_status_body("running", "a" * 500)
        assert len(body["description"]) == 140

    def test_custom_name(self) -> None:
        body = build_status_body("running", "x", name="my-check")
        assert body["name"] == "my-check"

    def test_empty_description_safe(self) -> None:
        body = build_status_body("running", "")
        assert body["description"] == ""


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class TestPipelineStatusClient:
    """Client unit tests with a stubbed httpx."""

    def _install_fake_httpx(
        self,
        monkeypatch: pytest.MonkeyPatch,
        response: _Response,
        capture: list[dict[str, Any]] | None = None,
    ) -> None:
        class _FakeHttpx:
            @staticmethod
            def post(
                url: str,
                headers: dict[str, str],
                json: dict[str, Any],
                timeout: float,
            ) -> _Response:
                if capture is not None:
                    capture.append({"url": url, "headers": headers, "json": json})
                return response

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)

    def test_create_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_fake_httpx(
            monkeypatch,
            _Response(201, {"id": 17, "status": "running", "target_url": ""}),
        )
        client = PipelineStatusClient(project_id=42, token="tok")
        result = client.create(sha="abc123", state="running")
        assert result is not None
        assert result.status_id == 17
        assert result.state == "running"

    def test_create_no_token_noop(self) -> None:
        client = PipelineStatusClient(project_id=42, token="")
        assert client.create(sha="abc") is None

    def test_update_maps_conclusion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap: list[dict[str, Any]] = []
        self._install_fake_httpx(
            monkeypatch,
            _Response(201, {"id": 1, "status": "success", "target_url": ""}),
            capture=cap,
        )
        client = PipelineStatusClient(project_id="ns%2Fproj", token="tok")
        result = client.update(sha="def", conclusion="success", summary="OK")
        assert result is not None
        assert cap[0]["json"]["state"] == "success"
        assert "ns%2Fproj" in cap[0]["url"]

    def test_non_2xx_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_fake_httpx(monkeypatch, _Response(500))
        client = PipelineStatusClient(project_id=1, token="t")
        assert client.create(sha="x") is None

    def test_invalid_state_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap: list[dict[str, Any]] = []
        self._install_fake_httpx(
            monkeypatch, _Response(201, {"id": 1, "status": "running", "target_url": ""}), capture=cap
        )
        client = PipelineStatusClient(project_id=1, token="t")
        client.create(sha="x", state="bogus-state")
        assert cap[0]["json"]["state"] == "running"

    def test_httpx_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "httpx", None)
        client = PipelineStatusClient(project_id=1, token="t")
        assert client.create(sha="x") is None
        # restore so other tests can patch httpx again
        monkeypatch.delitem(sys.modules, "httpx", raising=False)

    def test_unconfigured_property(self) -> None:
        assert PipelineStatusClient(project_id=0, token="t").configured is False
        assert PipelineStatusClient(project_id=1, token="").configured is False
        assert PipelineStatusClient(project_id=1, token="t").configured is True
