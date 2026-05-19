"""Tests for :mod:`bernstein.core.trackers.builtin.github_projects_adapter`."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from bernstein.core.trackers import (
    OptimisticConcurrencyError,
    RateLimited,
    TrackerUnavailable,
)
from bernstein.core.trackers.builtin.github_projects_adapter import (
    GITHUB_GRAPHQL_URL,
    GitHubProjectsV2Adapter,
    GitHubProjectsV2Config,
    _resolve_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _schema_response(*, owner_type: str = "organization") -> dict[str, Any]:
    """Return a canned project schema response."""
    project = {
        "id": "PVT_AAA",
        "fields": {
            "nodes": [
                {
                    "id": "FID_STATUS",
                    "name": "Status",
                    "dataType": "SINGLE_SELECT",
                    "options": [
                        {"id": "OPT_TODO", "name": "Todo"},
                        {"id": "OPT_PROGRESS", "name": "In Progress"},
                        {"id": "OPT_DONE", "name": "Done"},
                    ],
                },
                {
                    "id": "FID_CLI",
                    "name": "CLI",
                    "dataType": "SINGLE_SELECT",
                    "options": [
                        {"id": "OPT_CLAUDE", "name": "claude"},
                        {"id": "OPT_AIDER", "name": "aider"},
                    ],
                },
            ],
        },
    }
    block = {"data": {"organization": None, "user": None}}
    block["data"][owner_type] = {"projectV2": project}
    return block


def _items_page(
    *,
    cursor: str | None = None,
    has_next: bool = False,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nodes = items or [
        {
            "id": "PVTI_1",
            "content": {
                "__typename": "Issue",
                "id": "I_1",
                "number": 7,
                "title": "Refactor parser",
                "body": "Body 1",
                "url": "https://github.com/o/r/issues/7",
                "repository": {"nameWithOwner": "o/r"},
                "labels": {"nodes": [{"name": "bug"}, {"name": "P1"}]},
            },
            "fieldValues": {
                "nodes": [
                    {
                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                        "name": "In Progress",
                        "field": {"name": "Status"},
                    },
                    {
                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                        "name": "claude",
                        "field": {"name": "CLI"},
                    },
                ],
            },
        },
        {
            "id": "PVTI_2",
            "content": {
                "__typename": "PullRequest",
                "id": "PR_1",
                "number": 8,
                "title": "Add tests",
                "body": "Body 2",
                "url": "https://github.com/o/r/pull/8",
                "repository": {"nameWithOwner": "o/r"},
                "labels": {"nodes": []},
            },
            "fieldValues": {
                "nodes": [
                    {
                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                        "name": "Todo",
                        "field": {"name": "Status"},
                    },
                ],
            },
        },
    ]
    return {
        "data": {
            "node": {
                "items": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                },
            },
        },
    }


def _make_adapter(
    *,
    status_filter: str | None = None,
    cli_field: str | None = "CLI",
    status_map: dict[str, str] | None = None,
) -> GitHubProjectsV2Adapter:
    config = GitHubProjectsV2Config(
        project_owner="acme",
        project_number=42,
        status_field_name="Status",
        status_filter=status_filter,
        status_map=status_map or {},
        cli_choice_field_name=cli_field,
        pat_env="GH_TRACKER_PAT_TEST",
    )
    return GitHubProjectsV2Adapter(
        config=config,
        token_provider=lambda: "tok-test",
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_pat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PAT", "abc123")
    config = GitHubProjectsV2Config(
        project_owner="acme",
        project_number=1,
        pat_env="MY_PAT",
    )
    assert _resolve_token(config) == "abc123"


def test_resolve_token_falls_back_to_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_PAT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    config = GitHubProjectsV2Config(
        project_owner="acme",
        project_number=1,
    )
    assert _resolve_token(config) == "fallback"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("MY_PAT", raising=False)
    config = GitHubProjectsV2Config(
        project_owner="acme",
        project_number=1,
        pat_env="MY_PAT",
    )
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=_items_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 2
    assert len(tickets) == 2
    issue, pr = tickets
    assert issue.id == "PVTI_1"
    assert issue.title == "Refactor parser"
    assert issue.status == "In Progress"
    assert issue.labels == ("bug", "P1")
    assert issue.routing_hint.cli == "claude"
    assert issue.raw["content_id"] == "I_1"
    assert issue.raw["content_type"] == "Issue"
    assert pr.routing_hint.cli is None
    assert pr.raw["content_type"] == "PullRequest"


@respx.mock
def test_pull_open_tickets_filters_by_status() -> None:
    adapter = _make_adapter(status_filter="In Progress")
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=_items_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["PVTI_1"]


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter()
    page_one = _items_page(cursor="CUR1", has_next=True)
    page_two = _items_page(
        cursor=None,
        has_next=False,
        items=[
            {
                "id": "PVTI_3",
                "content": {
                    "__typename": "Issue",
                    "id": "I_3",
                    "number": 9,
                    "title": "Page two",
                    "body": "",
                    "url": "https://github.com/o/r/issues/9",
                    "repository": {"nameWithOwner": "o/r"},
                    "labels": {"nodes": []},
                },
                "fieldValues": {"nodes": []},
            },
        ],
    )
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["PVTI_1", "PVTI_2", "PVTI_3"]


@respx.mock
def test_pull_open_tickets_skips_drafts_by_default() -> None:
    adapter = _make_adapter()
    draft_only = _items_page(
        items=[
            {
                "id": "PVTI_DRAFT",
                "content": {
                    "__typename": "DraftIssue",
                    "id": "DI_1",
                    "title": "Draft",
                    "body": "tbd",
                },
                "fieldValues": {"nodes": []},
            },
        ],
    )
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=draft_only),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets == []


@respx.mock
def test_pull_open_tickets_includes_drafts_when_requested() -> None:
    adapter = _make_adapter()
    draft_only = _items_page(
        items=[
            {
                "id": "PVTI_DRAFT",
                "content": {
                    "__typename": "DraftIssue",
                    "id": "DI_1",
                    "title": "Draft",
                    "body": "tbd",
                },
                "fieldValues": {"nodes": []},
            },
        ],
    )
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=draft_only),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets({"include_drafts": True}))
    finally:
        adapter.close()
    assert len(tickets) == 1
    assert tickets[0].title == "Draft"


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_targets_underlying_subject() -> None:
    adapter = _make_adapter()
    route = respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "addComment": {
                        "commentEdge": {"node": {"id": "IC_1"}},
                    },
                },
            },
        )
    )
    try:
        result = adapter.add_comment("I_1", "hello", idempotency_key="k1")
    finally:
        adapter.close()

    assert result.comment_id == "IC_1"
    assert result.ticket_id == "I_1"
    # Inspect the captured mutation payload. Parse JSON rather than match
    # raw byte substrings so formatting / key-order changes in the request
    # serializer do not flap the assertions.
    sent = route.calls[0].request
    payload = json.loads(sent.content.decode("utf-8"))
    assert "addComment" in payload.get("query", "")
    variables = payload.get("variables") or {}
    assert variables.get("subjectId") == "I_1"
    assert variables.get("clientMutationId") == "k1"


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_resolves_option_id_by_name() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(
                200,
                json={
                    "data": {
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": "PVTI_1"},
                        },
                    },
                },
            ),
        ]
    )
    try:
        result = adapter.transition("PVTI_1", "Done", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "Done"
    assert result.ticket_id == "PVTI_1"


@respx.mock
def test_transition_uses_status_map() -> None:
    adapter = _make_adapter(status_map={"done": "Done"})
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(
                200,
                json={
                    "data": {
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": "PVTI_1"},
                        },
                    },
                },
            ),
        ]
    )
    try:
        result = adapter.transition("PVTI_1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"


@respx.mock
def test_transition_unknown_status_raises() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=_schema_response()),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("PVTI_1", "Mystery")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            429,
            json={"message": "slow down"},
            headers={"Retry-After": "17"},
        )
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 17.0


@respx.mock
def test_abuse_detection_rate_limit_403() -> None:
    """HTTP 403 abuse-detection responses surface as ``RateLimited``.

    GitHub returns 403 (not 429) for the abuse-detection / secondary
    rate-limit branch. The adapter must map this to the typed
    :class:`RateLimited` error so retry orchestration sees the same
    signal as a 429.
    """
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "You have exceeded a secondary rate limit"},
            headers={"Retry-After": "23"},
        )
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 23.0


@respx.mock
def test_secondary_rate_limit_typed_error() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "type": "RATE_LIMITED",
                        "message": "API rate limit exceeded",
                    },
                ],
            },
        )
    )
    try:
        with pytest.raises(RateLimited):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_etag_mismatch_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(412, json={"message": "Precondition Failed"}),
        ]
    )
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("PVTI_1", "Done", etag="W/abc")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(503, json={"message": "down"}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# CLI choice routing hint
# ---------------------------------------------------------------------------


@respx.mock
def test_cli_choice_field_disabled_when_unconfigured() -> None:
    adapter = _make_adapter(cli_field=None)
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response()),
            httpx.Response(200, json=_items_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert all(t.routing_hint.cli is None for t in tickets)


# ---------------------------------------------------------------------------
# Schema discovery edge cases
# ---------------------------------------------------------------------------


@respx.mock
def test_schema_discovery_falls_through_to_user_project() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_schema_response(owner_type="user")),
            httpx.Response(200, json=_items_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert len(tickets) == 2


@respx.mock
def test_schema_missing_project_raises() -> None:
    adapter = _make_adapter()
    respx.post(GITHUB_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"organization": None, "user": None}},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
