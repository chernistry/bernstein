"""Tests for :mod:`bernstein.core.trackers.builtin.plane_adapter`."""

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
from bernstein.core.trackers.builtin.plane_adapter import (
    DEFAULT_PLANE_URL,
    PlaneAdapter,
    PlaneConfig,
    _resolve_base_url,
    _resolve_token,
)

WORKSPACE = "acme"
PROJECT = "11111111-1111-1111-1111-111111111111"
BASE = "https://plane.test"
ISSUES_URL = f"{BASE}/api/v1/workspaces/{WORKSPACE}/projects/{PROJECT}/issues/"
STATES_URL = f"{BASE}/api/v1/workspaces/{WORKSPACE}/projects/{PROJECT}/states/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _states_response() -> dict[str, Any]:
    return {
        "results": [
            {"id": "S_TODO", "name": "Todo"},
            {"id": "S_PROGRESS", "name": "In Progress"},
            {"id": "S_DONE", "name": "Done"},
        ],
    }


def _issue(
    *,
    issue_id: str = "I_1",
    name: str = "Refactor parser",
    state_id: str = "S_PROGRESS",
    labels: list[dict[str, str]] | None = None,
    sequence_id: int = 7,
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "name": name,
        "description_html": "<p>Body</p>",
        "state": state_id,
        "sequence_id": sequence_id,
        "labels": labels if labels is not None else [{"name": "bug"}, {"name": "P1"}],
    }


def _issues_page(
    *,
    issues: list[dict[str, Any]] | None = None,
    next_cursor: int | None = None,
) -> dict[str, Any]:
    nodes = issues if issues is not None else [_issue()]
    payload: dict[str, Any] = {"results": nodes}
    if next_cursor is not None:
        payload["next_cursor"] = next_cursor
    return payload


def _make_adapter(
    *,
    state_filter: str | None = None,
    cli_prefix: str | None = "cli:",
    state_map: dict[str, str] | None = None,
) -> PlaneAdapter:
    config = PlaneConfig(
        workspace_slug=WORKSPACE,
        project_id=PROJECT,
        instance_url=BASE,
        api_token_env="PLANE_API_KEY_TEST",
        state_filter=state_filter,
        state_map=state_map or {},
        cli_choice_label_prefix=cli_prefix,
    )
    return PlaneAdapter(
        config=config,
        token_provider=lambda: "tok-test",
        base_url_provider=lambda: BASE,
    )


# ---------------------------------------------------------------------------
# Token + base URL resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PLANE_KEY", "k123")
    config = PlaneConfig(
        workspace_slug="w",
        project_id="p",
        api_token_env="MY_PLANE_KEY",
    )
    assert _resolve_token(config) == "k123"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANE_API_KEY", raising=False)
    monkeypatch.delenv("MY_PLANE_KEY", raising=False)
    config = PlaneConfig(
        workspace_slug="w",
        project_id="p",
        api_token_env="MY_PLANE_KEY",
    )
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


def test_resolve_base_url_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_URL", "https://plane.acme.internal/")
    config = PlaneConfig(
        workspace_slug="w",
        project_id="p",
        instance_url="https://api.plane.so",
    )
    assert _resolve_base_url(config) == "https://plane.acme.internal"


def test_resolve_base_url_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANE_URL", raising=False)
    config = PlaneConfig(workspace_slug="w", project_id="p", instance_url="")
    assert _resolve_base_url(config) == DEFAULT_PLANE_URL


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_issues_page(
                issues=[
                    _issue(
                        labels=[{"name": "bug"}, {"name": "cli:claude"}],
                    ),
                    _issue(
                        issue_id="I_2",
                        name="Add tests",
                        state_id="S_TODO",
                        labels=[],
                        sequence_id=8,
                    ),
                ],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert len(tickets) == 2
    first, second = tickets
    assert first.id == "I_1"
    assert first.title == "Refactor parser"
    assert first.status == "In Progress"
    assert "bug" in first.labels
    assert first.routing_hint.cli == "claude"
    assert first.raw["state_id"] == "S_PROGRESS"
    assert first.raw["workspace"] == WORKSPACE
    assert first.raw["sequence_id"] == 7
    assert "/issues/I_1" in first.external_url
    assert second.routing_hint.cli is None
    assert second.status == "Todo"


@respx.mock
def test_pull_open_tickets_filters_by_state() -> None:
    adapter = _make_adapter(state_filter="In Progress")
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_issues_page(
                issues=[
                    _issue(),
                    _issue(issue_id="I_2", state_id="S_TODO"),
                ],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["I_1"]


@respx.mock
def test_pull_open_tickets_paginates_via_next_cursor() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_issues_page(
                    issues=[_issue(issue_id="I_1")],
                    next_cursor=2,
                ),
            ),
            httpx.Response(
                200,
                json=_issues_page(issues=[_issue(issue_id="I_2")]),
            ),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["I_1", "I_2"]


@respx.mock
def test_pull_open_tickets_filter_overrides_config() -> None:
    adapter = _make_adapter(state_filter="In Progress")
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_issues_page(
                issues=[_issue(), _issue(issue_id="I_2", state_id="S_TODO")],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets({"state": "Todo"}))
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["I_2"]


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_to_issue_comments_endpoint() -> None:
    adapter = _make_adapter()
    comments_url = f"{ISSUES_URL}I_1/comments/"
    route = respx.post(comments_url).mock(
        return_value=httpx.Response(201, json={"id": "C_1", "comment_html": "hello"}),
    )
    try:
        result = adapter.add_comment("I_1", "hello", idempotency_key="k1")
    finally:
        adapter.close()
    assert result.comment_id == "C_1"
    assert result.ticket_id == "I_1"
    sent = route.calls[0].request
    assert sent.headers.get("X-API-Key") == "tok-test"
    assert sent.headers.get("Idempotency-Key") == "k1"
    assert b'"comment_html"' in sent.content
    assert b'"hello"' in sent.content


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_resolves_state_by_name() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    issue_url = f"{ISSUES_URL}I_1/"
    route = respx.patch(issue_url).mock(
        return_value=httpx.Response(200, json={"id": "I_1", "state": "S_DONE"}),
    )
    try:
        result = adapter.transition("I_1", "Done", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "Done"
    assert result.ticket_id == "I_1"
    sent = route.calls[0].request
    assert b'"state":"S_DONE"' in sent.content
    assert sent.headers.get("Idempotency-Key") == "k2"


@respx.mock
def test_transition_accepts_state_uuid() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.patch(f"{ISSUES_URL}I_1/").mock(
        return_value=httpx.Response(200, json={"id": "I_1"}),
    )
    try:
        result = adapter.transition("I_1", "S_DONE")
    finally:
        adapter.close()
    assert result.new_status == "S_DONE"


@respx.mock
def test_transition_uses_state_map() -> None:
    adapter = _make_adapter(state_map={"done": "Done"})
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.patch(f"{ISSUES_URL}I_1/").mock(
        return_value=httpx.Response(200, json={"id": "I_1"}),
    )
    try:
        result = adapter.transition("I_1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"


@respx.mock
def test_transition_unknown_state_raises() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("I_1", "Mystery")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(
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
def test_etag_mismatch_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.patch(f"{ISSUES_URL}I_1/").mock(return_value=httpx.Response(412, json={"message": "Precondition Failed"}))
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("I_1", "Done", etag="W/abc")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(503, json={"message": "down"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_4xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# CLI choice routing hint
# ---------------------------------------------------------------------------


@respx.mock
def test_cli_choice_label_disabled_when_unconfigured() -> None:
    adapter = _make_adapter(cli_prefix=None)
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_issues_page(
                issues=[_issue(labels=[{"name": "bug"}, {"name": "cli:claude"}])],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets[0].routing_hint.cli is None
    assert "cli:claude" in tickets[0].labels


# ---------------------------------------------------------------------------
# State discovery edge cases
# ---------------------------------------------------------------------------


@respx.mock
def test_state_discovery_handles_bare_list() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "S_TODO", "name": "Todo"},
                {"id": "S_DONE", "name": "Done"},
            ],
        )
    )
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_issues_page(issues=[_issue(state_id="S_TODO")]),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets[0].status == "Todo"


@respx.mock
def test_state_discovery_empty_raises() -> None:
    adapter = _make_adapter()
    respx.get(STATES_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


@respx.mock
def test_api_key_header_used_on_requests() -> None:
    adapter = _make_adapter()
    route = respx.get(STATES_URL).mock(return_value=httpx.Response(200, json=_states_response()))
    respx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=_issues_page(issues=[])))
    try:
        list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    sent = route.calls[0].request
    assert sent.headers.get("X-API-Key") == "tok-test"
    assert "Bearer" not in (sent.headers.get("Authorization") or "")
