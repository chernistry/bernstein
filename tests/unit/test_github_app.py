"""Tests for the GitHub App webhook foundation.

Covers:
- parse_webhook: extracts event_type, action, repo correctly
- verify_signature: validates HMAC-SHA256 correctly
- issue_to_tasks: generates correct task structure
- pr_comment_to_task: generates a fix task or None
- push_to_tasks: generates CI tasks for main-branch pushes
- POST /webhooks/github endpoint: 200 on valid event, 401 on bad signature
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from bernstein.core.server import create_app
from bernstein.github_app.mapper import issue_to_tasks, pr_comment_to_task, push_to_tasks
from bernstein.github_app.webhooks import WebhookEvent, parse_webhook, verify_signature

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def jsonl_path(tmp_path: Path) -> Path:
    return tmp_path / "tasks.jsonl"


@pytest.fixture()
def app(jsonl_path: Path) -> Any:
    return create_app(jsonl_path=jsonl_path)


@pytest.fixture()
async def client(app: Any) -> AsyncClient:  # type: ignore[misc]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_OBJ: dict[str, Any] = {"full_name": "owner/repo", "name": "repo"}

_ISSUE_PAYLOAD: dict[str, Any] = {
    "action": "opened",
    "repository": _REPO_OBJ,
    "issue": {
        "number": 42,
        "title": "Fix the broken thing",
        "body": "It keeps crashing when you do X.",
        "html_url": "https://github.com/owner/repo/issues/42",
        "labels": [{"name": "bug"}, {"name": "priority: critical"}],
    },
}

_PR_COMMENT_PAYLOAD: dict[str, Any] = {
    "action": "created",
    "repository": _REPO_OBJ,
    "pull_request": {"number": 7},
    "comment": {
        "body": "This function needs error handling.",
        "html_url": "https://github.com/owner/repo/pull/7#discussion_r123",
        "user": {"login": "reviewer"},
    },
}

_PUSH_PAYLOAD: dict[str, Any] = {
    "ref": "refs/heads/main",
    "repository": _REPO_OBJ,
    "before": "abc12345" * 5,
    "after": "def67890" * 5,
    "compare": "https://github.com/owner/repo/compare/abc...def",
    "pusher": {"name": "alice"},
    "commits": [
        {"id": "def67890abcdef00", "message": "feat: add widget\n\nLonger body"},
        {"id": "aabbccdd11223344", "message": "fix: typo"},
    ],
}


def _make_sig(body: bytes, secret: str) -> str:
    """Compute a valid X-Hub-Signature-256 header value."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _webhook_headers(event_type: str) -> dict[str, str]:
    return {
        "x-github-event": event_type,
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# parse_webhook tests
# ---------------------------------------------------------------------------


class TestParseWebhook:
    def test_extracts_event_type(self) -> None:
        headers = _webhook_headers("issues")
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        event = parse_webhook(headers, body)
        assert event.event_type == "issues"

    def test_extracts_action(self) -> None:
        headers = _webhook_headers("issues")
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        event = parse_webhook(headers, body)
        assert event.action == "opened"

    def test_extracts_repo(self) -> None:
        headers = _webhook_headers("issues")
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        event = parse_webhook(headers, body)
        assert event.repo == "owner/repo"

    def test_push_has_empty_action(self) -> None:
        """Push events have no 'action' field — should be empty string."""
        payload = {**_PUSH_PAYLOAD}
        headers = _webhook_headers("push")
        body = json.dumps(payload).encode()
        event = parse_webhook(headers, body)
        assert event.action == ""

    def test_case_insensitive_header(self) -> None:
        headers = {"X-GitHub-Event": "issues", "Content-Type": "application/json"}
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        event = parse_webhook(headers, body)
        assert event.event_type == "issues"

    def test_missing_event_header_raises(self) -> None:
        with pytest.raises(ValueError, match="X-GitHub-Event"):
            parse_webhook({}, json.dumps(_ISSUE_PAYLOAD).encode())

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="valid JSON"):
            parse_webhook(_webhook_headers("issues"), b"not-json")

    def test_missing_repository_raises(self) -> None:
        payload = {"action": "opened"}
        with pytest.raises(ValueError, match="repository"):
            parse_webhook(_webhook_headers("issues"), json.dumps(payload).encode())

    def test_payload_stored_in_event(self) -> None:
        headers = _webhook_headers("issues")
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        event = parse_webhook(headers, body)
        assert event.payload["action"] == "opened"


# ---------------------------------------------------------------------------
# verify_signature tests
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def test_valid_signature(self) -> None:
        body = b"hello"
        secret = "mysecret"
        sig = _make_sig(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_wrong_secret(self) -> None:
        body = b"hello"
        sig = _make_sig(body, "correct")
        assert verify_signature(body, sig, "wrong") is False

    def test_tampered_body(self) -> None:
        secret = "mysecret"
        sig = _make_sig(b"original", secret)
        assert verify_signature(b"tampered", sig, secret) is False

    def test_missing_sha256_prefix(self) -> None:
        body = b"hello"
        secret = "mysecret"
        mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # Provide raw hex without "sha256=" prefix
        assert verify_signature(body, mac, secret) is False

    def test_empty_signature(self) -> None:
        assert verify_signature(b"data", "", "secret") is False


# ---------------------------------------------------------------------------
# issue_to_tasks tests
# ---------------------------------------------------------------------------


class TestIssueToTasks:
    def _event(self, payload: dict[str, Any] | None = None) -> WebhookEvent:
        p = payload or _ISSUE_PAYLOAD
        return WebhookEvent(
            event_type="issues",
            action=p.get("action", "opened"),
            repo="owner/repo",
            payload=p,
        )

    def test_opened_produces_one_task(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert len(tasks) == 1

    def test_task_title_includes_issue_number(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert "#42" in tasks[0]["title"]

    def test_task_role_backend_for_bug_label(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert tasks[0]["role"] == "backend"

    def test_task_priority_critical_from_label(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert tasks[0]["priority"] == 1

    def test_security_label_routes_to_security_role(self) -> None:
        payload: dict[str, Any] = {
            **_ISSUE_PAYLOAD,
            "issue": {
                **_ISSUE_PAYLOAD["issue"],
                "labels": [{"name": "security"}],
            },
        }
        event = WebhookEvent(event_type="issues", action="opened", repo="owner/repo", payload=payload)
        tasks = issue_to_tasks(event)
        assert tasks[0]["role"] == "security"

    def test_description_contains_url(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert "https://github.com/owner/repo/issues/42" in tasks[0]["description"]

    def test_non_opened_action_returns_empty(self) -> None:
        payload = {**_ISSUE_PAYLOAD, "action": "closed"}
        event = WebhookEvent(event_type="issues", action="closed", repo="owner/repo", payload=payload)
        assert issue_to_tasks(event) == []

    def test_task_type_is_standard(self) -> None:
        tasks = issue_to_tasks(self._event())
        assert tasks[0]["task_type"] == "standard"


# ---------------------------------------------------------------------------
# pr_comment_to_task tests
# ---------------------------------------------------------------------------


class TestPrCommentToTask:
    def _event(self, payload: dict[str, Any] | None = None) -> WebhookEvent:
        p = payload or _PR_COMMENT_PAYLOAD
        return WebhookEvent(
            event_type="pull_request_review_comment",
            action=p.get("action", "created"),
            repo="owner/repo",
            payload=p,
        )

    def test_actionable_comment_produces_task(self) -> None:
        task = pr_comment_to_task(self._event())
        assert task is not None

    def test_task_title_includes_pr_number(self) -> None:
        task = pr_comment_to_task(self._event())
        assert task is not None
        assert "PR#7" in task["title"]

    def test_task_type_is_fix(self) -> None:
        task = pr_comment_to_task(self._event())
        assert task is not None
        assert task["task_type"] == "fix"

    def test_lgtm_comment_returns_none(self) -> None:
        payload: dict[str, Any] = {
            **_PR_COMMENT_PAYLOAD,
            "comment": {"body": "LGTM!", "user": {"login": "reviewer"}},
        }
        event = WebhookEvent(
            event_type="pull_request_review_comment",
            action="created",
            repo="owner/repo",
            payload=payload,
        )
        assert pr_comment_to_task(event) is None

    def test_approval_comment_returns_none(self) -> None:
        payload: dict[str, Any] = {
            **_PR_COMMENT_PAYLOAD,
            "comment": {"body": "Approved", "user": {"login": "reviewer"}},
        }
        event = WebhookEvent(
            event_type="pull_request_review_comment",
            action="created",
            repo="owner/repo",
            payload=payload,
        )
        assert pr_comment_to_task(event) is None

    def test_non_created_action_returns_none(self) -> None:
        event = WebhookEvent(
            event_type="pull_request_review_comment",
            action="deleted",
            repo="owner/repo",
            payload=_PR_COMMENT_PAYLOAD,
        )
        assert pr_comment_to_task(event) is None


# ---------------------------------------------------------------------------
# push_to_tasks tests
# ---------------------------------------------------------------------------


class TestPushToTasks:
    def _event(self, payload: dict[str, Any] | None = None) -> WebhookEvent:
        p = payload or _PUSH_PAYLOAD
        return WebhookEvent(event_type="push", action="", repo="owner/repo", payload=p)

    def test_main_push_produces_one_task(self) -> None:
        tasks = push_to_tasks(self._event())
        assert len(tasks) == 1

    def test_non_main_ref_returns_empty(self) -> None:
        payload = {**_PUSH_PAYLOAD, "ref": "refs/heads/feature-xyz"}
        event = WebhookEvent(event_type="push", action="", repo="owner/repo", payload=payload)
        assert push_to_tasks(event) == []

    def test_empty_commits_returns_empty(self) -> None:
        payload = {**_PUSH_PAYLOAD, "commits": []}
        event = WebhookEvent(event_type="push", action="", repo="owner/repo", payload=payload)
        assert push_to_tasks(event) == []

    def test_force_push_returns_empty(self) -> None:
        """Before SHA being all zeros = force-push, should be ignored."""
        payload = {**_PUSH_PAYLOAD, "before": "0" * 40}
        event = WebhookEvent(event_type="push", action="", repo="owner/repo", payload=payload)
        assert push_to_tasks(event) == []

    def test_task_role_is_qa(self) -> None:
        tasks = push_to_tasks(self._event())
        assert tasks[0]["role"] == "qa"

    def test_description_contains_commit_shas(self) -> None:
        tasks = push_to_tasks(self._event())
        assert "def67890" in tasks[0]["description"]

    def test_master_branch_also_triggers(self) -> None:
        payload = {**_PUSH_PAYLOAD, "ref": "refs/heads/master"}
        event = WebhookEvent(event_type="push", action="", repo="owner/repo", payload=payload)
        tasks = push_to_tasks(event)
        assert len(tasks) == 1


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestWebhookEndpoint:
    async def test_valid_event_returns_200(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_GITHUB_WEBHOOK_SECRET", raising=False)
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        resp = await client.post(
            "/webhooks/github",
            content=body,
            headers={"x-github-event": "issues", "content-type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_type"] == "issues"
        assert data["tasks_created"] == 1
        assert len(data["task_ids"]) == 1

    async def test_bad_signature_returns_401(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_GITHUB_WEBHOOK_SECRET", "real-secret")
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        resp = await client.post(
            "/webhooks/github",
            content=body,
            headers={
                "x-github-event": "issues",
                "content-type": "application/json",
                "x-hub-signature-256": "sha256=badhex",
            },
        )
        assert resp.status_code == 401

    async def test_valid_signature_accepted(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = "test-secret-xyz"
        monkeypatch.setenv("BERNSTEIN_GITHUB_WEBHOOK_SECRET", secret)
        body = json.dumps(_ISSUE_PAYLOAD).encode()
        sig = _make_sig(body, secret)
        resp = await client.post(
            "/webhooks/github",
            content=body,
            headers={
                "x-github-event": "issues",
                "content-type": "application/json",
                "x-hub-signature-256": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tasks_created"] == 1

    async def test_push_event_creates_task(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_GITHUB_WEBHOOK_SECRET", raising=False)
        body = json.dumps(_PUSH_PAYLOAD).encode()
        resp = await client.post(
            "/webhooks/github",
            content=body,
            headers={"x-github-event": "push", "content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["tasks_created"] == 1

    async def test_unknown_event_returns_200_no_tasks(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unrecognised event types should be accepted but produce no tasks."""
        monkeypatch.delenv("BERNSTEIN_GITHUB_WEBHOOK_SECRET", raising=False)
        payload = {"action": "ping", "repository": {"full_name": "owner/repo"}}
        body = json.dumps(payload).encode()
        resp = await client.post(
            "/webhooks/github",
            content=body,
            headers={"x-github-event": "ping", "content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["tasks_created"] == 0
