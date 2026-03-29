"""Tests for bernstein_sdk.adapters.github_actions."""

from __future__ import annotations

import pytest

from bernstein_sdk.adapters.github_actions import CIRunInfo, CITaskFactory
from bernstein_sdk.models import TaskComplexity, TaskScope


class TestCIRunInfo:
    def test_from_workflow_webhook_failure(self) -> None:
        payload = {
            "workflow_run": {
                "name": "CI",
                "id": 12345,
                "head_branch": "main",
                "head_sha": "abc1234567890",
                "conclusion": "failure",
                "html_url": "https://github.com/org/repo/actions/runs/12345",
            },
            "repository": {"full_name": "org/repo"},
        }
        run = CIRunInfo.from_workflow_webhook(payload)
        assert run is not None
        assert run.workflow_name == "CI"
        assert run.run_id == "12345"
        assert run.repository == "org/repo"
        assert run.branch == "main"
        assert run.commit_sha == "abc1234567890"
        assert run.conclusion == "failure"
        assert run.short_sha == "abc1234"
        assert run.branch_name == "main"

    def test_from_workflow_webhook_success_returns_none(self) -> None:
        payload = {
            "workflow_run": {
                "name": "CI",
                "id": 1,
                "head_branch": "main",
                "head_sha": "abc",
                "conclusion": "success",
            },
            "repository": {"full_name": "org/repo"},
        }
        assert CIRunInfo.from_workflow_webhook(payload) is None

    def test_from_workflow_webhook_empty_payload(self) -> None:
        assert CIRunInfo.from_workflow_webhook({}) is None

    def test_from_workflow_webhook_cancelled(self) -> None:
        payload = {
            "workflow_run": {
                "name": "Deploy",
                "id": 2,
                "head_branch": "feature/foo",
                "head_sha": "def456",
                "conclusion": "cancelled",
                "html_url": "",
            },
            "repository": {"full_name": "org/repo"},
        }
        run = CIRunInfo.from_workflow_webhook(payload)
        assert run is not None
        assert run.conclusion == "cancelled"

    def test_from_check_run_webhook_failure(self) -> None:
        payload = {
            "check_run": {
                "name": "tests",
                "id": 99,
                "head_sha": "sha789",
                "conclusion": "failure",
                "html_url": "https://github.com/org/repo/runs/99",
                "check_suite": {"head_branch": "main"},
            },
            "repository": {"full_name": "org/repo"},
        }
        run = CIRunInfo.from_check_run_webhook(payload)
        assert run is not None
        assert run.workflow_name == "tests"
        assert run.conclusion == "failure"

    def test_from_check_run_webhook_success_returns_none(self) -> None:
        payload = {
            "check_run": {
                "name": "tests",
                "id": 100,
                "head_sha": "sha",
                "conclusion": "success",
                "check_suite": {"head_branch": "main"},
            },
        }
        assert CIRunInfo.from_check_run_webhook(payload) is None

    def test_from_check_run_webhook_empty(self) -> None:
        assert CIRunInfo.from_check_run_webhook({}) is None

    def test_branch_name_strips_refs_prefix(self) -> None:
        run = CIRunInfo(
            workflow_name="CI",
            run_id="1",
            repository="org/repo",
            branch="refs/heads/feature/my-feature",
            commit_sha="abc123",
            conclusion="failure",
            run_url="",
        )
        assert run.branch_name == "feature/my-feature"

    def test_branch_name_no_prefix(self) -> None:
        run = CIRunInfo(
            workflow_name="CI",
            run_id="1",
            repository="org/repo",
            branch="main",
            commit_sha="abc123456",
            conclusion="failure",
            run_url="",
        )
        assert run.branch_name == "main"
        assert run.short_sha == "abc1234"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_WORKFLOW", "My Workflow")
        monkeypatch.setenv("GITHUB_RUN_ID", "9999")
        monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        monkeypatch.setenv("GITHUB_SHA", "deadbeef1234")
        run = CIRunInfo.from_env()
        assert run.workflow_name == "My Workflow"
        assert run.run_id == "9999"
        assert run.repository == "myorg/myrepo"
        assert run.conclusion == "failure"
        assert "myorg/myrepo" in run.run_url
        assert "9999" in run.run_url


class TestCITaskFactory:
    def _make_run(
        self,
        workflow: str = "CI",
        branch: str = "main",
        sha: str = "abc1234567890",
        repo: str = "org/repo",
        conclusion: str = "failure",
        run_url: str = "https://github.com/org/repo/runs/1",
    ) -> CIRunInfo:
        return CIRunInfo(
            workflow_name=workflow,
            run_id="42",
            repository=repo,
            branch=branch,
            commit_sha=sha,
            conclusion=conclusion,
            run_url=run_url,
        )

    def test_task_from_run_failure_priority(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(conclusion="failure")
        task = factory.task_from_run(run)
        assert task.priority == 1

    def test_task_from_run_timed_out_priority(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(conclusion="timed_out")
        task = factory.task_from_run(run)
        assert task.priority == 2

    def test_task_from_run_cancelled_priority(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(conclusion="cancelled")
        task = factory.task_from_run(run)
        assert task.priority == 3

    def test_task_from_run_title_format(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(workflow="Build", branch="main", sha="abc1234567890")
        task = factory.task_from_run(run)
        assert "Build" in task.title
        assert "main" in task.title
        assert "abc1234" in task.title

    def test_task_from_run_scope_and_complexity(self) -> None:
        factory = CITaskFactory()
        run = self._make_run()
        task = factory.task_from_run(run)
        assert task.scope == TaskScope.SMALL
        assert task.complexity == TaskComplexity.MEDIUM

    def test_task_from_run_external_ref(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(repo="org/repo")
        task = factory.task_from_run(run)
        assert task.external_ref.startswith("github_actions:")
        assert "org/repo" in task.external_ref

    def test_task_from_run_metadata(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(workflow="Tests", branch="main", sha="abc", repo="a/b")
        task = factory.task_from_run(run)
        assert task.metadata["ci_provider"] == "github_actions"
        assert task.metadata["workflow"] == "Tests"
        assert task.metadata["repository"] == "a/b"

    def test_task_from_run_description_includes_run_url(self) -> None:
        factory = CITaskFactory()
        run = self._make_run(run_url="https://github.com/org/repo/runs/42")
        task = factory.task_from_run(run)
        assert "https://github.com/org/repo/runs/42" in task.description

    def test_task_from_run_default_role_is_qa(self) -> None:
        factory = CITaskFactory()
        run = self._make_run()
        task = factory.task_from_run(run)
        assert task.role == "qa"

    def test_task_from_run_custom_role(self) -> None:
        factory = CITaskFactory(default_role="backend")
        run = self._make_run()
        task = factory.task_from_run(run)
        assert task.role == "backend"

    def test_task_from_workflow_webhook(self) -> None:
        factory = CITaskFactory()
        payload = {
            "workflow_run": {
                "name": "CI",
                "id": 1,
                "head_branch": "main",
                "head_sha": "abc123",
                "conclusion": "failure",
                "html_url": "",
            },
            "repository": {"full_name": "org/repo"},
        }
        task = factory.task_from_workflow_webhook(payload)
        assert task is not None
        assert task.role == "qa"

    def test_task_from_workflow_webhook_non_failure(self) -> None:
        factory = CITaskFactory()
        payload = {
            "workflow_run": {
                "name": "CI",
                "id": 1,
                "head_branch": "main",
                "head_sha": "abc",
                "conclusion": "success",
            },
        }
        assert factory.task_from_workflow_webhook(payload) is None

    def test_task_from_check_run_webhook(self) -> None:
        factory = CITaskFactory()
        payload = {
            "check_run": {
                "name": "tests",
                "id": 10,
                "head_sha": "sha",
                "conclusion": "failure",
                "html_url": "",
                "check_suite": {"head_branch": "main"},
            },
            "repository": {"full_name": "org/repo"},
        }
        task = factory.task_from_check_run_webhook(payload)
        assert task is not None

    def test_task_from_check_run_webhook_success(self) -> None:
        factory = CITaskFactory()
        payload = {
            "check_run": {
                "name": "tests",
                "id": 11,
                "head_sha": "sha",
                "conclusion": "success",
                "check_suite": {"head_branch": "main"},
            },
        }
        assert factory.task_from_check_run_webhook(payload) is None

    def test_task_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_WORKFLOW", "CI")
        monkeypatch.setenv("GITHUB_RUN_ID", "100")
        monkeypatch.setenv("GITHUB_REPOSITORY", "myorg/myrepo")
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        monkeypatch.setenv("GITHUB_SHA", "deadbeef12345")
        factory = CITaskFactory()
        task = factory.task_from_env()
        assert "CI" in task.title
        assert task.priority == 1  # "failure" → priority 1

    def test_custom_priority_mapping(self) -> None:
        factory = CITaskFactory(conclusion_to_priority={"cancelled": 1})
        run = self._make_run(conclusion="cancelled")
        task = factory.task_from_run(run)
        assert task.priority == 1
