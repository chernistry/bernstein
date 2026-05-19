"""Tests for :mod:`bernstein.core.trackers.builtin.jira_dc_adapter`."""

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
from bernstein.core.trackers.builtin.jira_dc_adapter import (
    CA_BUNDLE_ENV,
    JiraDataCenterAdapter,
    JiraDataCenterConfig,
    _resolve_token,
    _resolve_verify,
)

BASE_URL = "https://jira.example.internal"
SEARCH_URL = f"{BASE_URL}/rest/api/2/search"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _issue(
    *,
    key: str = "ENG-1",
    summary: str = "Refactor parser",
    description: str = "Body",
    status_name: str = "In Progress",
    labels: list[str] | None = None,
    cli_value: str | None = None,
    cli_field_id: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "status": {"name": status_name},
        "labels": labels if labels is not None else ["bug", "P1"],
        "issuetype": {"name": "Task"},
    }
    if cli_field_id and cli_value is not None:
        fields[cli_field_id] = {"value": cli_value}
    return {
        "id": "10001",
        "key": key,
        "fields": fields,
    }


def _search_response(
    *,
    issues: list[dict[str, Any]] | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    payload = issues if issues is not None else [_issue()]
    return {
        "issues": payload,
        "total": total if total is not None else len(payload),
        "startAt": 0,
        "maxResults": 50,
    }


def _make_adapter(
    *,
    project_key: str | None = None,
    status_map: dict[str, str] | None = None,
    cli_field_id: str | None = None,
    page_size: int = 50,
) -> JiraDataCenterAdapter:
    config = JiraDataCenterConfig(
        base_url=BASE_URL,
        pat_env="JIRA_DC_PAT_TEST",
        project_key=project_key,
        status_map=status_map or {},
        cli_choice_field_id=cli_field_id,
        page_size=page_size,
    )
    return JiraDataCenterAdapter(
        config=config,
        token_provider=lambda: "tok-test",
    )


# ---------------------------------------------------------------------------
# Token & TLS resolution
# ---------------------------------------------------------------------------


def test_resolve_token_from_pat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PAT", "pat-abc")
    config = JiraDataCenterConfig(base_url=BASE_URL, pat_env="MY_PAT")
    assert _resolve_token(config) == "pat-abc"


def test_resolve_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_PAT", raising=False)
    monkeypatch.delenv("JIRA_DC_PAT", raising=False)
    config = JiraDataCenterConfig(base_url=BASE_URL, pat_env="MY_PAT")
    with pytest.raises(TrackerUnavailable):
        _resolve_token(config)


def test_resolve_verify_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CA_BUNDLE_ENV, "/etc/bernstein/ca.pem")
    config = JiraDataCenterConfig(base_url=BASE_URL, verify_tls=True)
    assert _resolve_verify(config) == "/etc/bernstein/ca.pem"


def test_resolve_verify_falls_back_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)
    config = JiraDataCenterConfig(base_url=BASE_URL, verify_tls=False)
    assert _resolve_verify(config) is False


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter(cli_field_id="customfield_10100")
    issue = _issue(cli_field_id="customfield_10100", cli_value="claude")
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response(issues=[issue])),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.id == "ENG-1"
    assert ticket.title == "Refactor parser"
    assert ticket.status == "In Progress"
    assert ticket.labels == ("bug", "P1")
    assert ticket.routing_hint.cli == "claude"
    assert ticket.external_url == f"{BASE_URL}/browse/ENG-1"
    assert ticket.raw["issue_id"] == "10001"
    assert ticket.raw["project_key"] == "ENG"


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter(page_size=1)
    page_one = _search_response(issues=[_issue(key="ENG-1")], total=2)
    page_two = _search_response(issues=[_issue(key="ENG-2")], total=2)
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["ENG-1", "ENG-2"]


@respx.mock
def test_pull_open_tickets_uses_status_filter_when_no_jql() -> None:
    adapter = _make_adapter(project_key="ENG")
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response()),
    )
    try:
        list(adapter.pull_open_tickets({"status": "Open"}))
    finally:
        adapter.close()
    sent_jql = route.calls[0].request.url.params["jql"]
    assert 'project = "ENG"' in sent_jql
    assert 'status = "Open"' in sent_jql


@respx.mock
def test_pull_open_tickets_respects_jql_override() -> None:
    adapter = _make_adapter()
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response(issues=[])),
    )
    try:
        list(adapter.pull_open_tickets({"jql": 'labels = "ai-welcome"'}))
    finally:
        adapter.close()
    assert route.calls[0].request.url.params["jql"] == 'labels = "ai-welcome"'


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_to_issue() -> None:
    adapter = _make_adapter()
    comment_url = f"{BASE_URL}/rest/api/2/issue/ENG-1/comment"
    route = respx.post(comment_url).mock(
        return_value=httpx.Response(201, json={"id": "9001", "body": "hello"}),
    )
    try:
        result = adapter.add_comment("ENG-1", "hello", idempotency_key="k1")
    finally:
        adapter.close()
    assert result.comment_id == "9001"
    assert result.ticket_id == "ENG-1"
    assert route.calls[0].request.headers["X-Bernstein-Idempotency-Key"] == "k1"
    assert b'"body":"hello"' in route.calls[0].request.content


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_resolves_id_by_name() -> None:
    adapter = _make_adapter()
    transitions_url = f"{BASE_URL}/rest/api/2/issue/ENG-1/transitions"
    respx.get(transitions_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "transitions": [
                    {"id": "31", "name": "Done", "to": {"name": "Done"}},
                    {"id": "11", "name": "Open", "to": {"name": "Open"}},
                ],
            },
        )
    )
    post_route = respx.post(transitions_url).mock(
        return_value=httpx.Response(204, json={}),
    )
    try:
        result = adapter.transition("ENG-1", "Done", idempotency_key="k2")
    finally:
        adapter.close()
    assert result.new_status == "Done"
    assert b'"id":"31"' in post_route.calls[0].request.content


@respx.mock
def test_transition_uses_status_map() -> None:
    adapter = _make_adapter(status_map={"done": "Done"})
    transitions_url = f"{BASE_URL}/rest/api/2/issue/ENG-1/transitions"
    respx.get(transitions_url).mock(
        return_value=httpx.Response(
            200,
            json={"transitions": [{"id": "31", "name": "Done", "to": {"name": "Done"}}]},
        )
    )
    respx.post(transitions_url).mock(return_value=httpx.Response(204, json={}))
    try:
        result = adapter.transition("ENG-1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"


@respx.mock
def test_transition_unknown_status_raises() -> None:
    adapter = _make_adapter()
    transitions_url = f"{BASE_URL}/rest/api/2/issue/ENG-1/transitions"
    respx.get(transitions_url).mock(
        return_value=httpx.Response(200, json={"transitions": []}),
    )
    try:
        with pytest.raises(TrackerUnavailable):
            adapter.transition("ENG-1", "Mystery")
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            429,
            json={"errorMessages": ["slow down"]},
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
    transitions_url = f"{BASE_URL}/rest/api/2/issue/ENG-1/transitions"
    respx.get(transitions_url).mock(
        return_value=httpx.Response(
            200,
            json={"transitions": [{"id": "31", "name": "Done", "to": {"name": "Done"}}]},
        )
    )
    respx.post(transitions_url).mock(
        return_value=httpx.Response(412, json={"errorMessages": ["mismatch"]}),
    )
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("ENG-1", "Done", etag="W/abc")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(500, json={"errorMessages": ["boom"]}),
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
    adapter = _make_adapter()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(issues=[_issue()]),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets[0].routing_hint.cli is None
