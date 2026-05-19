"""Tests for :mod:`bernstein.core.trackers.builtin.asana_adapter`."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from bernstein.core.trackers import (
    RateLimited,
    TrackerUnavailable,
)
from bernstein.core.trackers.builtin.asana_adapter import (
    ASANA_API_BASE,
    AsanaAdapter,
    AsanaConfig,
    _resolve_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _task(
    *,
    gid: str = "TASK_1",
    name: str = "Refactor parser",
    notes: str = "Body 1",
    section_gid: str = "SEC_PROGRESS",
    section_name: str = "In Progress",
    permalink: str = "https://app.asana.com/0/PRJ/TASK_1",
    tags: list[str] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
    completed: bool = False,
) -> dict[str, Any]:
    return {
        "gid": gid,
        "name": name,
        "notes": notes,
        "permalink_url": permalink,
        "completed": completed,
        "memberships": [{"section": {"gid": section_gid, "name": section_name}}],
        "tags": [{"name": t} for t in (tags or [])],
        "custom_fields": custom_fields or [],
    }


def _tasks_page(
    *,
    tasks: list[dict[str, Any]] | None = None,
    next_offset: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "data": tasks
        if tasks is not None
        else [
            _task(
                gid="TASK_1",
                name="Refactor parser",
                section_gid="SEC_PROGRESS",
                section_name="In Progress",
                tags=["bug", "P1"],
                custom_fields=[
                    {
                        "gid": "CF_CLI",
                        "name": "CLI",
                        "enum_value": {"name": "claude"},
                    },
                ],
            ),
            _task(
                gid="TASK_2",
                name="Add tests",
                notes="Body 2",
                section_gid="SEC_TODO",
                section_name="Todo",
                permalink="https://app.asana.com/0/PRJ/TASK_2",
            ),
        ],
    }
    if next_offset:
        body["next_page"] = {"offset": next_offset}
    else:
        body["next_page"] = None
    return body


def _make_adapter(
    *,
    section_filter_gid: str | None = None,
    cli_cf_gid: str | None = "CF_CLI",
    section_map: dict[str, str] | None = None,
    include_completed: bool = False,
) -> AsanaAdapter:
    config = AsanaConfig(
        workspace_gid="WS_1",
        project_gid="PRJ_1",
        section_filter_gid=section_filter_gid,
        section_map=section_map or {},
        cli_choice_custom_field_gid=cli_cf_gid,
        include_completed=include_completed,
        pat_env="ASANA_PAT_TEST",
    )
    return AsanaAdapter(
        config=config,
        token_provider=lambda: "tok-test",
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_ASANA_PAT", "abc123")
    config = AsanaConfig(
        workspace_gid="WS_1",
        project_gid="PRJ_1",
        pat_env="MY_ASANA_PAT",
    )
    assert _resolve_token(config) == "abc123"


def test_resolve_token_falls_back_to_default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_ASANA_PAT", raising=False)
    monkeypatch.setenv("ASANA_PERSONAL_ACCESS_TOKEN", "fallback")
    config = AsanaConfig(workspace_gid="WS_1", project_gid="PRJ_1")
    assert _resolve_token(config) == "fallback"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASANA_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MY_ASANA_PAT", raising=False)
    config = AsanaConfig(
        workspace_gid="WS_1",
        project_gid="PRJ_1",
        pat_env="MY_ASANA_PAT",
    )
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(200, json=_tasks_page())
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1
    assert len(tickets) == 2
    first, second = tickets
    assert first.id == "TASK_1"
    assert first.title == "Refactor parser"
    assert first.status == "In Progress"
    assert first.labels == ("bug", "P1")
    assert first.routing_hint.cli == "claude"
    assert first.raw["section_gid"] == "SEC_PROGRESS"
    assert first.external_url == "https://app.asana.com/0/PRJ/TASK_1"
    assert second.routing_hint.cli is None
    assert second.raw["section_gid"] == "SEC_TODO"


@respx.mock
def test_pull_open_tickets_filters_by_section() -> None:
    adapter = _make_adapter(section_filter_gid="SEC_PROGRESS")
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(return_value=httpx.Response(200, json=_tasks_page()))
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["TASK_1"]


@respx.mock
def test_pull_open_tickets_filter_override_via_arg() -> None:
    adapter = _make_adapter(section_filter_gid="SEC_PROGRESS")
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(return_value=httpx.Response(200, json=_tasks_page()))
    try:
        tickets = list(adapter.pull_open_tickets({"section": "SEC_TODO"}))
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["TASK_2"]


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter()
    page_one = _tasks_page(
        tasks=[_task(gid="TASK_1", name="Page one")],
        next_offset="OFFSET_2",
    )
    page_two = _tasks_page(
        tasks=[_task(gid="TASK_3", name="Page two", section_gid="SEC_DONE", section_name="Done")],
    )
    route = respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        side_effect=[
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["TASK_1", "TASK_3"]
    assert route.call_count == 2
    second_request = route.calls[1].request
    assert b"offset=OFFSET_2" in second_request.url.query


@respx.mock
def test_pull_open_tickets_passes_completed_since_when_open_only() -> None:
    adapter = _make_adapter(include_completed=False)
    route = respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(200, json=_tasks_page(tasks=[])),
    )
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    request = route.calls[0].request
    assert b"completed_since=now" in request.url.query


@respx.mock
def test_pull_open_tickets_omits_completed_since_when_include_completed() -> None:
    adapter = _make_adapter(include_completed=True)
    route = respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(200, json=_tasks_page(tasks=[])),
    )
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    request = route.calls[0].request
    assert b"completed_since" not in request.url.query


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_story_with_idempotency_header() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{ASANA_API_BASE}/tasks/TASK_1/stories").mock(
        return_value=httpx.Response(
            201,
            json={"data": {"gid": "STORY_1", "text": "hello"}},
        )
    )
    try:
        result = adapter.add_comment("TASK_1", "hello", idempotency_key="k1")
    finally:
        adapter.close()

    assert result.comment_id == "STORY_1"
    assert result.ticket_id == "TASK_1"
    sent = route.calls[0].request
    body = json.loads(sent.content)
    assert body == {"data": {"text": "hello"}}
    assert sent.headers["X-Idempotency-Key"] == "k1"
    assert sent.headers["Authorization"] == "Bearer tok-test"


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_moves_task_to_section_by_gid() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{ASANA_API_BASE}/sections/SEC_DONE/addTask").mock(
        return_value=httpx.Response(200, json={"data": {}}),
    )
    try:
        result = adapter.transition("TASK_1", "SEC_DONE", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "SEC_DONE"
    assert result.ticket_id == "TASK_1"
    sent = route.calls[0].request
    body = json.loads(sent.content)
    assert body == {"data": {"task": "TASK_1"}}
    assert sent.headers["X-Idempotency-Key"] == "k2"


@respx.mock
def test_transition_resolves_section_via_section_map() -> None:
    adapter = _make_adapter(section_map={"done": "SEC_DONE"})
    route = respx.post(f"{ASANA_API_BASE}/sections/SEC_DONE/addTask").mock(
        return_value=httpx.Response(200, json={"data": {}}),
    )
    try:
        result = adapter.transition("TASK_1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"
    assert route.call_count == 1


@respx.mock
def test_transition_empty_section_gid_raises() -> None:
    adapter = _make_adapter(section_map={"done": ""})
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("TASK_1", "done")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# update_custom_field
# ---------------------------------------------------------------------------


@respx.mock
def test_update_custom_field_puts_task() -> None:
    adapter = _make_adapter()
    route = respx.put(f"{ASANA_API_BASE}/tasks/TASK_1").mock(
        return_value=httpx.Response(200, json={"data": {"gid": "TASK_1"}}),
    )
    try:
        adapter.update_custom_field(
            "TASK_1",
            "CF_AGENT",
            "claude",
            idempotency_key="k3",
        )
    finally:
        adapter.close()
    sent = route.calls[0].request
    body = json.loads(sent.content)
    assert body == {"data": {"custom_fields": {"CF_AGENT": "claude"}}}
    assert sent.headers["X-Idempotency-Key"] == "k3"


# ---------------------------------------------------------------------------
# Rate-limit & error handling
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(
            429,
            json={"errors": [{"message": "slow down"}]},
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
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(503, json={"errors": [{"message": "down"}]}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_4xx_other_than_429_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(403, json={"errors": [{"message": "forbidden"}]}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_payload_errors_field_surfaces_as_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(
            200,
            json={"errors": [{"message": "schema error"}]},
        )
    )
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# CLI choice custom field
# ---------------------------------------------------------------------------


@respx.mock
def test_cli_choice_custom_field_disabled_when_unconfigured() -> None:
    adapter = _make_adapter(cli_cf_gid=None)
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(200, json=_tasks_page()),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert all(t.routing_hint.cli is None for t in tickets)


@respx.mock
def test_cli_choice_reads_text_value_when_enum_absent() -> None:
    adapter = _make_adapter()
    page = _tasks_page(
        tasks=[
            _task(
                gid="TASK_X",
                name="Text CF",
                custom_fields=[
                    {"gid": "CF_CLI", "name": "CLI", "text_value": "codex"},
                ],
            ),
        ],
    )
    respx.get(f"{ASANA_API_BASE}/projects/PRJ_1/tasks").mock(
        return_value=httpx.Response(200, json=page),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert len(tickets) == 1
    assert tickets[0].routing_hint.cli == "codex"
