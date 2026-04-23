"""Unit tests for the ticket import command and providers."""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.ticket_cmd import (
    build_task_payload,
    from_ticket,
    infer_role,
    infer_scope,
)
from bernstein.core.integrations.tickets import (
    TicketAuthError,
    TicketParseError,
    TicketPayload,
    fetch_ticket,
)

# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def _payload(source: str = "github") -> TicketPayload:
    return TicketPayload(
        id="ENG-1",
        title="Example",
        description="body",
        labels=(),
        assignee=None,
        url="https://example.test",
        source=source,  # type: ignore[arg-type]
    )


def test_routes_linear_web_url_to_linear_provider() -> None:
    with patch(
        "bernstein.core.integrations.tickets.linear.fetch_linear",
        return_value=_payload("linear"),
    ) as mock_linear:
        result = fetch_ticket("https://linear.app/acme/issue/ENG-123")
    mock_linear.assert_called_once()
    assert result.source == "linear"


def test_routes_linear_scheme_to_linear_provider() -> None:
    with patch(
        "bernstein.core.integrations.tickets.linear.fetch_linear",
        return_value=_payload("linear"),
    ) as mock_linear:
        fetch_ticket("linear://ENG-42")
    mock_linear.assert_called_once()


def test_routes_github_url_to_github_provider() -> None:
    with patch(
        "bernstein.core.integrations.tickets.github_issues.fetch_github_issue",
        return_value=_payload("github"),
    ) as mock_gh:
        result = fetch_ticket("https://github.com/acme/widgets/issues/7")
    mock_gh.assert_called_once()
    assert result.source == "github"


def test_routes_jira_url_to_jira_provider() -> None:
    with patch(
        "bernstein.core.integrations.tickets.jira.fetch_jira",
        return_value=_payload("jira"),
    ) as mock_jira:
        fetch_ticket("https://acme.atlassian.net/browse/ENG-9")
    mock_jira.assert_called_once()


def test_unrecognized_url_raises_parse_error() -> None:
    with pytest.raises(TicketParseError):
        fetch_ticket("https://example.com/not/a/ticket")


# ---------------------------------------------------------------------------
# Linear auth
# ---------------------------------------------------------------------------


def test_linear_raises_auth_error_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.integrations.tickets import linear

    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    with pytest.raises(TicketAuthError) as excinfo:
        linear.fetch_linear("https://linear.app/acme/issue/ENG-1")
    assert "LINEAR_API_KEY" in str(excinfo.value)


def test_linear_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.integrations.tickets import linear

    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")

    response = {
        "data": {
            "issue": {
                "identifier": "ENG-42",
                "title": "Fix login",
                "description": "It is broken.",
                "url": "https://linear.app/acme/issue/ENG-42",
                "labels": {"nodes": [{"name": "bug"}, {"name": "frontend"}]},
                "assignee": {"displayName": "Ada Lovelace", "name": "ada"},
            }
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = response

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        payload = linear.fetch_linear("https://linear.app/acme/issue/ENG-42")

    mock_post.assert_called_once()
    assert payload.id == "ENG-42"
    assert payload.title == "Fix login"
    assert payload.labels == ("bug", "frontend")
    assert payload.assignee == "Ada Lovelace"
    assert payload.source == "linear"


# ---------------------------------------------------------------------------
# GitHub: gh CLI path and REST fallback
# ---------------------------------------------------------------------------


def test_github_gh_cli_path_parses() -> None:
    from bernstein.core.integrations.tickets import github_issues

    gh_stdout = json.dumps(
        {
            "number": 7,
            "title": "Add tests",
            "body": "We need more.",
            "labels": [{"name": "docs"}],
            "assignees": [{"login": "alice"}],
            "url": "https://github.com/acme/widgets/issues/7",
        }
    )

    proc = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=gh_stdout, stderr="")

    with (
        patch.object(github_issues, "_gh_available", return_value=True),
        patch.object(github_issues.subprocess, "run", return_value=proc) as mock_run,
    ):
        payload = github_issues.fetch_github_issue("https://github.com/acme/widgets/issues/7")

    mock_run.assert_called_once()
    assert payload.id == "acme/widgets#7"
    assert payload.title == "Add tests"
    assert payload.labels == ("docs",)
    assert payload.assignee == "alice"
    assert payload.source == "github"


def test_github_rest_fallback_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.integrations.tickets import github_issues

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

    rest_body = {
        "number": 11,
        "title": "Refactor",
        "body": "Split the function.",
        "labels": [{"name": "backend"}, "refactor"],
        "assignee": {"login": "bob"},
        "html_url": "https://github.com/acme/widgets/issues/11",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = rest_body

    with (
        patch.object(github_issues, "_gh_available", return_value=False),
        patch("httpx.get", return_value=mock_resp) as mock_get,
    ):
        payload = github_issues.fetch_github_issue("https://github.com/acme/widgets/issues/11")

    mock_get.assert_called_once()
    assert payload.id == "acme/widgets#11"
    assert payload.title == "Refactor"
    assert payload.labels == ("backend", "refactor")
    assert payload.assignee == "bob"


def test_github_rest_missing_token_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.integrations.tickets import github_issues

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch.object(github_issues, "_gh_available", return_value=False):
        with pytest.raises(TicketAuthError) as excinfo:
            github_issues.fetch_github_issue("https://github.com/acme/widgets/issues/1")
    assert "GITHUB_TOKEN" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Jira parsing (incl. ADF description)
# ---------------------------------------------------------------------------


def test_jira_parses_adf_description(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.integrations.tickets import jira

    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")

    adf_description = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "First paragraph. "},
                    {"type": "text", "text": "More text."},
                ],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Second paragraph."}],
            },
        ],
    }
    body = {
        "key": "ENG-9",
        "fields": {
            "summary": "Improve logging",
            "description": adf_description,
            "labels": ["security", "backend"],
            "assignee": {"displayName": "Carol"},
        },
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = body

    with patch("httpx.get", return_value=mock_resp):
        payload = jira.fetch_jira("https://acme.atlassian.net/browse/ENG-9")

    assert payload.id == "ENG-9"
    assert payload.title == "Improve logging"
    assert "First paragraph" in payload.description
    assert "Second paragraph" in payload.description
    assert payload.labels == ("security", "backend")
    assert payload.assignee == "Carol"
    assert payload.source == "jira"


# ---------------------------------------------------------------------------
# Role / scope inference
# ---------------------------------------------------------------------------


def test_role_inference_bug_to_qa() -> None:
    assert infer_role(("bug",)) == "qa"


def test_role_inference_docs_to_docs() -> None:
    assert infer_role(("documentation",)) == "docs"
    assert infer_role(("docs",)) == "docs"


def test_role_inference_fallback_to_default() -> None:
    assert infer_role(("weird", "unseen")) == "backend"
    assert infer_role((), default="custom") == "custom"


def test_scope_inference() -> None:
    assert infer_scope(("small",)) == "small"
    assert infer_scope(("epic",)) == "large"
    assert infer_scope(()) == "medium"


# ---------------------------------------------------------------------------
# CLI: dry-run, metadata, --run
# ---------------------------------------------------------------------------


def _fake_ticket(**overrides: Any) -> TicketPayload:
    defaults: dict[str, Any] = {
        "id": "ENG-1",
        "title": "Fix things",
        "description": "Lots to fix.",
        "labels": ("bug",),
        "assignee": "alice",
        "url": "https://linear.app/acme/issue/ENG-1",
        "source": "linear",
    }
    defaults.update(overrides)
    return TicketPayload(**defaults)


def test_dry_run_does_not_call_task_store() -> None:
    runner = CliRunner()
    with (
        patch("bernstein.cli.commands.ticket_cmd.fetch_ticket", return_value=_fake_ticket()),
        patch("bernstein.cli.commands.ticket_cmd.server_post") as mock_post,
    ):
        result = runner.invoke(
            from_ticket,
            ["https://linear.app/acme/issue/ENG-1", "--dry-run"],
        )
    assert result.exit_code == 0, result.output
    mock_post.assert_not_called()
    # Dry-run should emit the payload JSON
    assert "metadata" in result.output
    assert "ENG-1" in result.output


def test_task_payload_carries_source_and_external_id() -> None:
    ticket = _fake_ticket(source="jira", id="ENG-42", labels=("docs",))
    payload = build_task_payload(ticket, role=None, priority=None)
    assert payload["metadata"] == {
        "source": "jira",
        "external_id": "ENG-42",
        "url": "https://linear.app/acme/issue/ENG-1",
    }
    # Role inferred from label
    assert payload["role"] == "docs"


def test_create_path_posts_to_server_and_honors_run_flag() -> None:
    runner = CliRunner()
    fake_post = MagicMock(return_value={"id": "tsk_abc"})
    with (
        patch("bernstein.cli.commands.ticket_cmd.fetch_ticket", return_value=_fake_ticket()),
        patch("bernstein.cli.commands.ticket_cmd.server_post", fake_post),
        patch("bernstein.cli.commands.ticket_cmd.subprocess.call", return_value=0) as mock_call,
    ):
        result = runner.invoke(
            from_ticket,
            ["https://linear.app/acme/issue/ENG-1", "--run", "--priority", "high"],
        )

    assert result.exit_code == 0, result.output
    fake_post.assert_called_once()
    posted_path, posted_payload = fake_post.call_args.args
    assert posted_path == "/tasks"
    # --priority high maps to 1
    assert posted_payload["priority"] == 1
    mock_call.assert_called_once()
    assert mock_call.call_args.args[0][-1] == "tsk_abc"


def test_explicit_role_flag_overrides_inference() -> None:
    ticket = _fake_ticket(labels=("bug",))
    payload = build_task_payload(ticket, role="backend", priority="low")
    assert payload["role"] == "backend"
    assert payload["priority"] == 3
