"""Tests for :mod:`bernstein.core.trackers.builtin.gitlab_adapter`."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import respx

from bernstein.core.trackers import RateLimited, TrackerUnavailable
from bernstein.core.trackers.builtin.gitlab_adapter import (
    DEFAULT_GITLAB_URL,
    GITLAB_API_PATH,
    GitLabAdapter,
    GitLabConfig,
    _project_segment,
    _resolve_base_url,
    _resolve_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _issue(
    *,
    iid: int,
    title: str = "Title",
    body: str = "Body",
    labels: list[str] | None = None,
    state: str = "opened",
    web_url: str = "https://gitlab.com/o/r/-/issues/1",
    assignee: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": 1000 + iid,
        "iid": iid,
        "project_id": 7,
        "title": title,
        "description": body,
        "labels": labels or [],
        "state": state,
        "web_url": web_url,
        "assignee": assignee,
    }


def _make_adapter(
    *,
    instance_url: str | None = None,
    label_filter_pull: tuple[str, ...] = (),
    assignee_filter_pull: str | None = None,
    state_label_map: dict[str, str] | None = None,
    cli_choice_label_prefix: str | None = None,
    project_id_or_path: str = "my-group/my-project",
) -> GitLabAdapter:
    config = GitLabConfig(
        project_id_or_path=project_id_or_path,
        instance_url=instance_url,
        token_env="GITLAB_TEST_TOKEN",
        label_filter_pull=label_filter_pull,
        assignee_filter_pull=assignee_filter_pull,
        state_label_map=state_label_map or {},
        cli_choice_label_prefix=cli_choice_label_prefix,
    )
    return GitLabAdapter(
        config=config,
        token_provider=lambda: "tok-test",
    )


# ---------------------------------------------------------------------------
# Token / URL resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "primary-pat")
    config = GitLabConfig(project_id_or_path="o/r")
    assert _resolve_token(config) == "primary-pat"


def test_resolve_token_uses_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setenv("CUSTOM_GL_TOKEN", "custom-pat")
    config = GitLabConfig(project_id_or_path="o/r", token_env="CUSTOM_GL_TOKEN")
    assert _resolve_token(config) == "custom-pat"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    config = GitLabConfig(project_id_or_path="o/r")
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


def test_resolve_base_url_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_URL", raising=False)
    config = GitLabConfig(project_id_or_path="o/r")
    assert _resolve_base_url(config) == DEFAULT_GITLAB_URL


def test_resolve_base_url_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.acme.example/")
    config = GitLabConfig(project_id_or_path="o/r")
    assert _resolve_base_url(config) == "https://gitlab.acme.example"


def test_resolve_base_url_config_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_URL", "https://from-env.example")
    config = GitLabConfig(
        project_id_or_path="o/r",
        instance_url="https://from-config.example/",
    )
    assert _resolve_base_url(config) == "https://from-config.example"


def test_project_segment_encodes_path() -> None:
    config = GitLabConfig(project_id_or_path="my-group/my-project")
    assert _project_segment(config) == "my-group%2Fmy-project"


def test_project_segment_passes_numeric_id_through() -> None:
    config = GitLabConfig(project_id_or_path="12345")
    assert _project_segment(config) == "12345"


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


def _issues_url(project: str = "my-group%2Fmy-project") -> str:
    return f"{DEFAULT_GITLAB_URL}{GITLAB_API_PATH}/projects/{project}/issues"


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter(
        state_label_map={"ready": "state:ready", "claim": "state:claimed"},
        cli_choice_label_prefix="cli:",
    )
    route = respx.get(_issues_url()).mock(
        return_value=httpx.Response(
            200,
            json=[
                _issue(
                    iid=7,
                    title="Refactor parser",
                    body="Body 1",
                    labels=["bug", "P1", "state:ready", "cli:claude"],
                ),
                _issue(
                    iid=8,
                    title="Add tests",
                    body="Body 2",
                    labels=[],
                ),
            ],
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1
    assert len(tickets) == 2
    first, second = tickets
    assert first.id == "7"
    assert first.title == "Refactor parser"
    assert first.status == "ready"
    assert first.labels == ("bug", "P1", "state:ready", "cli:claude")
    assert first.routing_hint.cli == "claude"
    assert first.raw["project_id"] == 7
    assert second.status == ""
    assert second.routing_hint.cli is None


@respx.mock
def test_pull_open_tickets_paginates_via_page_param() -> None:
    adapter = _make_adapter()
    page_one = [_issue(iid=i) for i in range(1, 51)]
    page_two = [_issue(iid=51, title="Page two only")]
    route = respx.get(_issues_url()).mock(
        side_effect=[
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 2
    assert [t.id for t in tickets][-1] == "51"
    # The second call must request page=2.
    second_request = route.calls[1].request
    assert b"page=2" in second_request.url.query


@respx.mock
def test_pull_open_tickets_passes_labels_and_assignee() -> None:
    adapter = _make_adapter(
        label_filter_pull=("ai-welcome",),
        assignee_filter_pull="bot",
    )
    route = respx.get(_issues_url()).mock(return_value=httpx.Response(200, json=[]))
    try:
        list(adapter.pull_open_tickets({"labels": ["P1"]}))
    finally:
        adapter.close()

    request = route.calls[0].request
    query = request.url.query.decode()
    assert "labels=ai-welcome%2CP1" in query
    assert "assignee_username=bot" in query
    assert "state=opened" in query


@respx.mock
def test_pull_open_tickets_filter_assignee_overrides_config() -> None:
    adapter = _make_adapter(assignee_filter_pull="bot")
    route = respx.get(_issues_url()).mock(return_value=httpx.Response(200, json=[]))
    try:
        list(adapter.pull_open_tickets({"assignee": "other"}))
    finally:
        adapter.close()

    request = route.calls[0].request
    assert "assignee_username=other" in request.url.query.decode()


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_note() -> None:
    adapter = _make_adapter()
    route = respx.post(_issues_url() + "/7/notes").mock(
        return_value=httpx.Response(201, json={"id": 999, "body": "hello"})
    )
    try:
        result = adapter.add_comment("7", "hello", idempotency_key="k1")
    finally:
        adapter.close()

    assert result.comment_id == "999"
    assert result.ticket_id == "7"
    request = route.calls[0].request
    assert b'"body":"hello"' in request.content
    assert request.headers.get("idempotency-key") == "k1"


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_swaps_state_labels_atomically() -> None:
    adapter = _make_adapter(
        state_label_map={
            "claim": "state:claimed",
            "ready": "state:ready",
            "failed": "state:failed",
        },
    )
    route = respx.put(_issues_url() + "/7").mock(
        return_value=httpx.Response(200, json={"iid": 7, "labels": ["state:ready"]})
    )
    try:
        result = adapter.transition("7", "ready", idempotency_key="k2")
    finally:
        adapter.close()

    assert result.new_status == "ready"
    assert result.ticket_id == "7"
    request = route.calls[0].request
    body = request.content
    assert b'"add_labels":"state:ready"' in body
    # Both other state labels are in remove_labels (order doesn't matter,
    # we just need both to be present).
    match = re.search(rb'"remove_labels":"([^"]+)"', body)
    assert match is not None
    removed = sorted(match.group(1).decode().split(","))
    assert removed == ["state:claimed", "state:failed"]
    assert request.headers.get("idempotency-key") == "k2"


@respx.mock
def test_transition_without_map_uses_status_id_as_label() -> None:
    adapter = _make_adapter()
    route = respx.put(_issues_url() + "/7").mock(return_value=httpx.Response(200, json={"iid": 7}))
    try:
        result = adapter.transition("7", "needs-review")
    finally:
        adapter.close()

    assert result.new_status == "needs-review"
    body = route.calls[0].request.content
    assert b'"add_labels":"needs-review"' in body
    assert b"remove_labels" not in body


# ---------------------------------------------------------------------------
# Self-hosted URL override
# ---------------------------------------------------------------------------


@respx.mock
def test_self_hosted_instance_url_is_used() -> None:
    adapter = _make_adapter(instance_url="https://gitlab.acme.example")
    route = respx.get(f"https://gitlab.acme.example{GITLAB_API_PATH}/projects/my-group%2Fmy-project/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1


@respx.mock
def test_self_hosted_via_gitlab_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.self.example")
    monkeypatch.setenv("GITLAB_TEST_TOKEN", "tok")
    config = GitLabConfig(
        project_id_or_path="12345",
        token_env="GITLAB_TEST_TOKEN",
    )
    adapter = GitLabAdapter(config=config)
    route = respx.get(f"https://gitlab.self.example{GITLAB_API_PATH}/projects/12345/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


@respx.mock
def test_authorization_header_is_set() -> None:
    adapter = _make_adapter()
    route = respx.get(_issues_url()).mock(return_value=httpx.Response(200, json=[]))
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    auth = route.calls[0].request.headers.get("authorization")
    assert auth == "Bearer tok-test"


# ---------------------------------------------------------------------------
# Rate-limit & errors
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(_issues_url()).mock(
        return_value=httpx.Response(
            429,
            json={"message": "slow down"},
            headers={"Retry-After": "13"},
        )
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 13.0


@respx.mock
def test_plain_forbidden_is_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(_issues_url()).mock(
        return_value=httpx.Response(
            403,
            json={"message": "forbidden"},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_forbidden_with_rate_limit_headers_is_rate_limited() -> None:
    adapter = _make_adapter()
    respx.get(_issues_url()).mock(
        return_value=httpx.Response(
            403,
            json={"message": "rate-limited"},
            headers={"Retry-After": "7"},
        )
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 7.0


@respx.mock
def test_server_error_is_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(_issues_url()).mock(return_value=httpx.Response(503, text="upstream down"))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_client_error_is_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(_issues_url()).mock(return_value=httpx.Response(404, text="not found"))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
