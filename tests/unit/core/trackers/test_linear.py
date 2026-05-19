"""Tests for :mod:`bernstein.core.trackers.linear`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from bernstein.core.trackers import (
    OptimisticConcurrencyError,
    RateLimited,
    TrackerUnavailable,
)
from bernstein.core.trackers.linear import (
    LINEAR_GRAPHQL_URL,
    LinearConfig,
    LinearTracker,
    _resolve_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _team_response() -> dict[str, Any]:
    """Return a canned team-and-states GraphQL response."""
    return {
        "data": {
            "teams": {
                "nodes": [
                    {
                        "id": "team-uuid-1",
                        "key": "ENG",
                        "name": "Engineering",
                        "states": {
                            "nodes": [
                                {"id": "state-todo", "name": "Todo", "type": "unstarted"},
                                {"id": "state-progress", "name": "In Progress", "type": "started"},
                                {"id": "state-done", "name": "Done", "type": "completed"},
                            ],
                        },
                    },
                ],
            },
        },
    }


def _issues_page(
    *,
    cursor: str | None = None,
    has_next: bool = False,
    nodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    default_nodes = [
        {
            "id": "issue-uuid-1",
            "identifier": "ENG-7",
            "title": "Refactor parser",
            "description": "Body 1",
            "url": "https://linear.app/acme/issue/ENG-7",
            "updatedAt": "2026-05-19T10:00:00.000Z",
            "state": {"id": "state-progress", "name": "In Progress"},
            "labels": {"nodes": [{"name": "bug"}, {"name": "cli/claude"}]},
        },
        {
            "id": "issue-uuid-2",
            "identifier": "ENG-8",
            "title": "Add tests",
            "description": "Body 2",
            "url": "https://linear.app/acme/issue/ENG-8",
            "updatedAt": "2026-05-19T11:00:00.000Z",
            "state": {"id": "state-todo", "name": "Todo"},
            "labels": {"nodes": []},
        },
    ]
    return {
        "data": {
            "issues": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": nodes if nodes is not None else default_nodes,
            },
        },
    }


def _make_adapter(
    *,
    state_filter: str | None = None,
    label_routing_field: str | None = "cli/",
    state_map: dict[str, str] | None = None,
) -> LinearTracker:
    config = LinearConfig(
        team_key="ENG",
        state_filter=state_filter,
        state_map=state_map or {},
        label_routing_field=label_routing_field,
        api_key_env="LINEAR_API_KEY_TEST",
    )
    return LinearTracker(
        config=config,
        token_provider=lambda: "lin_test_token",
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_abc123")
    config = LinearConfig(team_key="ENG")
    assert _resolve_token(config) == "lin_abc123"


def test_resolve_token_from_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_LINEAR_KEY", "lin_custom")
    config = LinearConfig(team_key="ENG", api_key_env="MY_LINEAR_KEY")
    assert _resolve_token(config) == "lin_custom"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("MY_LINEAR_KEY", raising=False)
    config = LinearConfig(team_key="ENG", api_key_env="MY_LINEAR_KEY")
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(200, json=_issues_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 2
    assert len(tickets) == 2
    first, second = tickets
    assert first.id == "issue-uuid-1"
    assert first.title == "Refactor parser"
    assert first.status == "In Progress"
    assert first.labels == ("bug", "cli/claude")
    assert first.routing_hint.cli == "claude"
    assert first.raw["identifier"] == "ENG-7"
    assert first.raw["team_key"] == "ENG"
    assert first.etag == "2026-05-19T10:00:00.000Z"
    assert second.routing_hint.cli is None


@respx.mock
def test_pull_open_tickets_filters_by_state() -> None:
    adapter = _make_adapter(state_filter="In Progress")
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(200, json=_issues_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["issue-uuid-1"]


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter()
    page_one = _issues_page(cursor="CUR1", has_next=True)
    page_two = _issues_page(
        cursor=None,
        has_next=False,
        nodes=[
            {
                "id": "issue-uuid-3",
                "identifier": "ENG-9",
                "title": "Page two",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-9",
                "updatedAt": "2026-05-19T12:00:00.000Z",
                "state": {"id": "state-todo", "name": "Todo"},
                "labels": {"nodes": []},
            },
        ],
    )
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["issue-uuid-1", "issue-uuid-2", "issue-uuid-3"]


@respx.mock
def test_pull_open_tickets_filter_state_override() -> None:
    adapter = _make_adapter(state_filter="Todo")
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(200, json=_issues_page()),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets({"state": "In Progress"}))
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["issue-uuid-1"]


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_mutation() -> None:
    adapter = _make_adapter()
    route = respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": "comment-uuid-1"},
                    },
                },
            },
        )
    )
    try:
        result = adapter.add_comment("issue-uuid-1", "hello world", idempotency_key="k1")
    finally:
        adapter.close()

    assert result.comment_id == "comment-uuid-1"
    assert result.ticket_id == "issue-uuid-1"
    sent = route.calls[0].request
    assert b"commentCreate" in sent.content
    assert b"issue-uuid-1" in sent.content
    # Idempotency key is appended as an HTML-comment marker.
    assert b"bernstein-idempotency:k1" in sent.content


@respx.mock
def test_add_comment_raises_when_success_false() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"commentCreate": {"success": False, "comment": None}}},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.add_comment("issue-uuid-1", "hi")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_resolves_state_by_name() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(
                200,
                json={
                    "data": {
                        "issueUpdate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid-1",
                                "updatedAt": "2026-05-19T13:00:00.000Z",
                            },
                        },
                    },
                },
            ),
        ]
    )
    try:
        result = adapter.transition("issue-uuid-1", "Done", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "Done"
    assert result.ticket_id == "issue-uuid-1"
    assert result.etag == "2026-05-19T13:00:00.000Z"


@respx.mock
def test_transition_uses_state_map() -> None:
    adapter = _make_adapter(state_map={"done": "Done"})
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(
                200,
                json={
                    "data": {
                        "issueUpdate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid-1",
                                "updatedAt": "2026-05-19T14:00:00.000Z",
                            },
                        },
                    },
                },
            ),
        ]
    )
    try:
        result = adapter.transition("issue-uuid-1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"


@respx.mock
def test_transition_accepts_state_id_directly() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(
                200,
                json={
                    "data": {
                        "issueUpdate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid-1",
                                "updatedAt": "2026-05-19T15:00:00.000Z",
                            },
                        },
                    },
                },
            ),
        ]
    )
    try:
        result = adapter.transition("issue-uuid-1", "state-done")
    finally:
        adapter.close()
    assert result.new_status == "state-done"


@respx.mock
def test_transition_unknown_state_raises() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=_team_response()),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("issue-uuid-1", "Mystery")
    finally:
        adapter.close()


@respx.mock
def test_transition_raises_when_success_false() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(
                200,
                json={"data": {"issueUpdate": {"success": False, "issue": None}}},
            ),
        ]
    )
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("issue-uuid-1", "Done")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            429,
            json={"message": "slow down"},
            headers={"Retry-After": "11"},
        )
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 11.0


@respx.mock
def test_secondary_rate_limit_typed_error() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "API rate limit exceeded",
                        "extensions": {"code": "RATELIMITED"},
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
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(412, json={"message": "Precondition Failed"}),
        ]
    )
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("issue-uuid-1", "Done", etag="2026-05-19T10:00:00.000Z")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(503, json={"message": "down"}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_401_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Label-based CLI routing hint
# ---------------------------------------------------------------------------


@respx.mock
def test_cli_routing_disabled_when_unconfigured() -> None:
    adapter = _make_adapter(label_routing_field=None)
    respx.post(LINEAR_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=_team_response()),
            httpx.Response(200, json=_issues_page()),
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
def test_schema_missing_team_raises() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"teams": {"nodes": []}}},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_graphql_top_level_error_surfaces_as_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(LINEAR_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"errors": [{"message": "broken", "extensions": {"code": "INTERNAL"}}]},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
