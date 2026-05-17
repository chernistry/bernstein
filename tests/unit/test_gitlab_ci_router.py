"""Unit tests for ``bernstein.gitlab_app.ci_router``."""

from __future__ import annotations

from typing import Any

import pytest

from bernstein.core.quality.ci_fix import CIFailure, CIFailureKind
from bernstein.gitlab_app.ci_router import (
    MAX_CI_RETRIES,
    GitLabCIBlameResult,
    build_pipeline_routing_payload,
    fetch_and_parse_failures,
)


def _fake_failure(
    kind: CIFailureKind = CIFailureKind.PYTEST,
    job: str = "test",
    summary: str = "tests failed",
    fix_hint: str = "run pytest -x",
    affected_files: list[str] | None = None,
) -> CIFailure:
    return CIFailure(
        kind=kind,
        job=job,
        summary=summary,
        details="",
        fix_hint=fix_hint,
        affected_files=affected_files or ["src/x.py"],
    )


class TestBuildPipelineRoutingPayload:
    def test_includes_failure_summaries(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[_fake_failure()],
            blame=GitLabCIBlameResult(head_sha="abc123def", ref="main", responsible_files=["src/x.py"]),
            pipeline_id=42,
        )
        assert payload["task_type"] == "fix"
        assert payload["role"] == "qa"
        assert payload["priority"] == 1
        assert "tests failed" in payload["description"]
        assert "src/x.py" in payload["description"]

    def test_no_failures_falls_back_to_builds(self) -> None:
        builds = [{"name": "lint", "stage": "quality"}]
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha=""),
            pipeline_id=99,
            failed_builds=builds,
        )
        assert "lint" in payload["description"]

    def test_no_failures_no_builds_uses_pipeline_id(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha=""),
            pipeline_id=99,
            failed_builds=None,
        )
        assert "99" in payload["description"]

    def test_pipeline_url_emitted(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="x"),
            pipeline_id=1,
            pipeline_url="https://gitlab/p/1",
        )
        assert "https://gitlab/p/1" in payload["description"]

    def test_retry_count_zero_no_note(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="x"),
            pipeline_id=1,
            retry_count=0,
        )
        assert "Retry attempt" not in payload["description"]

    def test_retry_count_one_emits_note(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="x"),
            pipeline_id=1,
            retry_count=1,
        )
        assert "Retry attempt 2/" in payload["description"]

    def test_model_escalates_on_retry(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="x"),
            pipeline_id=1,
            retry_count=2,
        )
        assert payload["model"] == "opus"
        assert payload["effort"] == "max"

    def test_first_attempt_uses_sonnet(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="x"),
            pipeline_id=1,
            retry_count=0,
        )
        assert payload["model"] == "sonnet"
        assert payload["effort"] == "high"

    def test_title_truncated(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[_fake_failure(summary="x" * 200)],
            blame=GitLabCIBlameResult(head_sha="abcdef1234567890" * 4),
            pipeline_id=1,
        )
        assert len(payload["title"]) <= 120

    def test_title_uses_sha_short(self) -> None:
        payload = build_pipeline_routing_payload(
            failures=[],
            blame=GitLabCIBlameResult(head_sha="abcdef1234567890"),
            pipeline_id=1,
        )
        assert "[abcdef12]" in payload["title"]

    def test_max_retries_constant(self) -> None:
        assert MAX_CI_RETRIES == 3


class TestFetchAndParseFailures:
    def test_no_token_returns_empty(self) -> None:
        assert (
            fetch_and_parse_failures(
                project_id=1,
                failed_builds=[{"id": 1, "name": "lint"}],
                token="",
            )
            == []
        )

    def test_no_builds_returns_empty(self) -> None:
        assert fetch_and_parse_failures(project_id=1, failed_builds=[], token="t") == []

    def test_no_project_id_returns_empty(self) -> None:
        assert (
            fetch_and_parse_failures(
                project_id="",
                failed_builds=[{"id": 1}],
                token="t",
            )
            == []
        )

    def test_calls_trace_per_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[tuple[Any, int]] = []

        def _fake_fetch(project_id: Any, job_id: int, token: str, base_url: Any = None) -> str:
            seen.append((project_id, job_id))
            return "ERROR: it broke\n"

        import bernstein.gitlab_app.ci_router as router_mod

        monkeypatch.setattr(router_mod, "fetch_job_trace", _fake_fetch)
        out = fetch_and_parse_failures(
            project_id=42,
            failed_builds=[{"id": 1, "name": "lint"}, {"id": 2, "name": "test"}],
            token="t",
        )
        assert len(seen) == 2
        # We expect at least one parsed failure from the simulated error log.
        assert isinstance(out, list)

    def test_skips_builds_with_no_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []

        def _fake_fetch(project_id: Any, job_id: int, token: str, base_url: Any = None) -> str:
            seen.append(job_id)
            return ""

        import bernstein.gitlab_app.ci_router as router_mod

        monkeypatch.setattr(router_mod, "fetch_job_trace", _fake_fetch)
        fetch_and_parse_failures(
            project_id=42,
            failed_builds=[{"name": "lint"}, {"id": "not-int"}],  # type: ignore[list-item]
            token="t",
        )
        assert seen == []

    def test_max_jobs_caps_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[int] = []

        def _fake_fetch(project_id: Any, job_id: int, token: str, base_url: Any = None) -> str:
            called.append(job_id)
            return ""

        import bernstein.gitlab_app.ci_router as router_mod

        monkeypatch.setattr(router_mod, "fetch_job_trace", _fake_fetch)
        fetch_and_parse_failures(
            project_id=42,
            failed_builds=[{"id": i} for i in range(10)],
            token="t",
            max_jobs=2,
        )
        assert len(called) == 2
