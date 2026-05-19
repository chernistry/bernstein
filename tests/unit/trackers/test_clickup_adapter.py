"""Tests for :mod:`bernstein.core.trackers.builtin.clickup_adapter`."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import respx

from bernstein.core.trackers import (
    OptimisticConcurrencyError,
    RateLimited,
    TrackerUnavailable,
)
from bernstein.core.trackers.builtin.clickup_adapter import (
    CLICKUP_API_BASE,
    ClickUpAdapter,
    ClickUpConfig,
    _resolve_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TASK_LIST_URL = re.compile(re.escape(f"{CLICKUP_API_BASE}/list/") + r".+/task")
_TASK_DETAIL_URL = re.compile(re.escape(f"{CLICKUP_API_BASE}/task/") + r"[^/]+$")
_TASK_COMMENT_URL = re.compile(re.escape(f"{CLICKUP_API_BASE}/task/") + r"[^/]+/comment")


def _tasks_page(
    *,
    tasks: list[dict[str, Any]] | None = None,
    last_page: bool = True,
) -> dict[str, Any]:
    nodes = tasks or [
        {
            "id": "abc123",
            "name": "Refactor parser",
            "description": "Body 1",
            "text_content": "Body 1",
            "url": "https://app.clickup.com/t/abc123",
            "status": {"status": "in progress", "type": "open"},
            "tags": [{"name": "bug"}, {"name": "p1"}],
            "list": {"id": "L1"},
            "space": {"id": "S1"},
            "custom_fields": [
                {
                    "id": "cf-cli",
                    "name": "CLI",
                    "value": "claude",
                },
            ],
        },
        {
            "id": "def456",
            "name": "Add tests",
            "description": "Body 2",
            "url": "https://app.clickup.com/t/def456",
            "status": {"status": "to do", "type": "open"},
            "tags": [],
            "list": {"id": "L1"},
            "space": {"id": "S1"},
            "custom_fields": [],
        },
    ]
    payload: dict[str, Any] = {"tasks": nodes}
    if last_page:
        payload["last_page"] = True
    return payload


def _make_adapter(
    *,
    status_filter: str | None = None,
    cli_field: str | None = "cf-cli",
    status_map: dict[str, str] | None = None,
    page_size: int = 100,
) -> ClickUpAdapter:
    config = ClickUpConfig(
        list_id="L1",
        workspace_id="W1",
        space_id="S1",
        status_filter=status_filter,
        status_map=status_map or {},
        cli_choice_custom_field_id=cli_field,
        token_env="CLICKUP_TEST_TOKEN",
        page_size=page_size,
    )
    return ClickUpAdapter(
        config=config,
        token_provider=lambda: "tok-test",
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_abc")
    config = ClickUpConfig(list_id="L1")
    assert _resolve_token(config) == "pk_abc"


def test_resolve_token_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_CLICKUP", "pk_xyz")
    config = ClickUpConfig(list_id="L1", token_env="MY_CLICKUP")
    assert _resolve_token(config) == "pk_xyz"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("MY_CLICKUP", raising=False)
    config = ClickUpConfig(list_id="L1", token_env="MY_CLICKUP")
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1
    assert len(tickets) == 2
    issue, other = tickets
    assert issue.id == "abc123"
    assert issue.title == "Refactor parser"
    assert issue.status == "in progress"
    assert issue.labels == ("bug", "p1")
    assert issue.routing_hint.cli == "claude"
    assert issue.raw["task_id"] == "abc123"
    assert issue.raw["list_id"] == "L1"
    assert other.routing_hint.cli is None
    assert other.status == "to do"


@respx.mock
def test_pull_open_tickets_filters_by_status() -> None:
    adapter = _make_adapter(status_filter="in progress")
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["abc123"]


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter(page_size=2)
    page_one = _tasks_page(last_page=False)
    page_two = _tasks_page(
        last_page=True,
        tasks=[
            {
                "id": "ghi789",
                "name": "Page two",
                "description": "",
                "url": "https://app.clickup.com/t/ghi789",
                "status": {"status": "to do"},
                "tags": [],
                "list": {"id": "L1"},
                "space": {"id": "S1"},
                "custom_fields": [],
            },
        ],
    )
    route = respx.get(_TASK_LIST_URL).mock(
        side_effect=[
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ],
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert route.call_count == 2
    assert [t.id for t in tickets] == ["abc123", "def456", "ghi789"]


@respx.mock
def test_pull_open_tickets_passes_archived_flag() -> None:
    adapter = _make_adapter()
    route = respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        list(adapter.pull_open_tickets({"include_archived": True}))
    finally:
        adapter.close()
    request = route.calls[0].request
    assert b"archived=true" in request.url.query


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_to_task_endpoint() -> None:
    adapter = _make_adapter()
    route = respx.post(_TASK_COMMENT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"id": "cmt-1", "hist_id": "hist-9"},
        ),
    )
    try:
        result = adapter.add_comment("abc123", "hello", idempotency_key="k1")
    finally:
        adapter.close()

    assert result.comment_id == "cmt-1"
    assert result.ticket_id == "abc123"
    sent = route.calls[0].request
    assert sent.url.path.endswith("/task/abc123/comment")
    assert b"hello" in sent.content
    assert b"bernstein-idempotency: k1" in sent.content


@respx.mock
def test_add_comment_without_idempotency_key_omits_marker() -> None:
    adapter = _make_adapter()
    route = respx.post(_TASK_COMMENT_URL).mock(
        return_value=httpx.Response(200, json={"id": "cmt-2"}),
    )
    try:
        adapter.add_comment("abc123", "hello")
    finally:
        adapter.close()
    assert b"bernstein-idempotency" not in route.calls[0].request.content


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_sends_status_name() -> None:
    adapter = _make_adapter()
    route = respx.put(_TASK_DETAIL_URL).mock(
        return_value=httpx.Response(200, json={"id": "abc123", "status": {"status": "complete"}}),
    )
    try:
        result = adapter.transition("abc123", "complete", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "complete"
    assert result.ticket_id == "abc123"
    sent = route.calls[0].request
    assert sent.url.path.endswith("/task/abc123")
    assert b'"status":"complete"' in sent.content


@respx.mock
def test_transition_uses_status_map() -> None:
    adapter = _make_adapter(status_map={"done": "complete"})
    route = respx.put(_TASK_DETAIL_URL).mock(
        return_value=httpx.Response(200, json={"id": "abc123"}),
    )
    try:
        result = adapter.transition("abc123", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"
    assert b'"status":"complete"' in route.calls[0].request.content


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(
            429,
            json={"err": "Rate limit reached", "ECODE": "RATE_001"},
            headers={"Retry-After": "13"},
        ),
    )
    try:
        with pytest.raises(RateLimited) as exc:
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert exc.value.retry_after == 13.0


@respx.mock
def test_inline_rate_limit_error_payload() -> None:
    adapter = _make_adapter()
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={"err": "Rate limit reached", "ECODE": "RATE_002"},
        ),
    )
    try:
        with pytest.raises(RateLimited):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_etag_mismatch_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    respx.put(_TASK_DETAIL_URL).mock(
        return_value=httpx.Response(412, json={"err": "Precondition Failed"}),
    )
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("abc123", "complete", etag="W/abc")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(503, json={"err": "down"}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_4xx_error_payload_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={"err": "List not found", "ECODE": "LIST_001"},
        ),
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
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert all(t.routing_hint.cli is None for t in tickets)


@respx.mock
def test_cli_choice_field_supports_dict_value() -> None:
    adapter = _make_adapter()
    payload = _tasks_page(
        tasks=[
            {
                "id": "abc999",
                "name": "Dict-shaped CLI value",
                "description": "",
                "url": "https://app.clickup.com/t/abc999",
                "status": {"status": "to do"},
                "tags": [],
                "list": {"id": "L1"},
                "space": {"id": "S1"},
                "custom_fields": [
                    {
                        "id": "cf-cli",
                        "value": {"name": "codex"},
                    },
                ],
            },
        ],
    )
    respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=payload),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert len(tickets) == 1
    assert tickets[0].routing_hint.cli == "codex"


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


@respx.mock
def test_token_is_sent_in_authorization_header() -> None:
    adapter = _make_adapter()
    route = respx.get(_TASK_LIST_URL).mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert route.calls[0].request.headers.get("Authorization") == "tok-test"
