"""Tests for :mod:`bernstein.core.trackers.builtin.jira_cloud_adapter`."""

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
from bernstein.core.trackers.builtin.jira_cloud_adapter import (
    JiraCloudConfig,
    JiraCloudTracker,
    _resolve_basic_auth,
    _resolve_domain,
)

DOMAIN = "acme.atlassian.net"
SEARCH_URL = f"https://{DOMAIN}/rest/api/3/search"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _issue(
    *,
    key: str = "ACME-1",
    summary: str = "Refactor parser",
    status: str = "In Progress",
    labels: list[str] | None = None,
    description: str | dict[str, Any] = "Body text",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = {
        "summary": summary,
        "description": description,
        "status": {"name": status},
        "labels": labels or [],
    }
    if extra:
        fields.update(extra)
    return {"id": "10001", "key": key, "fields": fields}


def _search_response(
    *,
    issues: list[dict[str, Any]] | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    issues = issues or [_issue()]
    return {"issues": issues, "total": total if total is not None else len(issues)}


def _make_adapter(
    *,
    cli_field: str | None = None,
    status_map: dict[str, str] | None = None,
    jql: str | None = None,
) -> JiraCloudTracker:
    config = JiraCloudConfig(
        domain=DOMAIN,
        jql=jql or "project = ACME AND statusCategory != Done",
        status_map=status_map or {},
        cli_choice_field_id=cli_field,
    )
    return JiraCloudTracker(
        config=config,
        auth_provider=lambda: "Basic dGVzdDp0ZXN0",
        domain_provider=lambda: DOMAIN,
    )


# ---------------------------------------------------------------------------
# Auth resolution
# ---------------------------------------------------------------------------


def test_resolve_basic_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_CLOUD_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_CLOUD_API_TOKEN", "secret")
    config = JiraCloudConfig(domain=DOMAIN)
    header = _resolve_basic_auth(config)
    assert header.startswith("Basic ")
    # base64("user@example.com:secret") == "dXNlckBleGFtcGxlLmNvbTpzZWNyZXQ="
    assert header == "Basic dXNlckBleGFtcGxlLmNvbTpzZWNyZXQ="


def test_resolve_basic_auth_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_CLOUD_EMAIL", "user@example.com")
    monkeypatch.delenv("JIRA_CLOUD_API_TOKEN", raising=False)
    config = JiraCloudConfig(domain=DOMAIN)
    with pytest.raises(TrackerUnavailable):
        _resolve_basic_auth(config)


def test_resolve_basic_auth_missing_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_CLOUD_EMAIL", raising=False)
    monkeypatch.setenv("JIRA_CLOUD_API_TOKEN", "secret")
    config = JiraCloudConfig(domain=DOMAIN)
    with pytest.raises(TrackerUnavailable):
        _resolve_basic_auth(config)


def test_resolve_domain_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_CLOUD_DOMAIN", "acme.atlassian.net/")
    config = JiraCloudConfig()
    assert _resolve_domain(config) == "acme.atlassian.net"


def test_resolve_domain_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_CLOUD_DOMAIN", raising=False)
    config = JiraCloudConfig()
    with pytest.raises(TrackerUnavailable):
        _resolve_domain(config)


def test_resolve_domain_from_config_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_CLOUD_DOMAIN", "other.atlassian.net")
    config = JiraCloudConfig(domain="acme.atlassian.net")
    assert _resolve_domain(config) == "acme.atlassian.net"


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                issues=[
                    _issue(key="ACME-1", labels=["bug", "P1"]),
                    _issue(
                        key="ACME-2",
                        summary="Add tests",
                        status="To Do",
                        description={
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "ADF body"}],
                                }
                            ],
                        },
                    ),
                ],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()

    assert route.call_count == 1
    assert len(tickets) == 2
    first, second = tickets
    assert first.id == "ACME-1"
    assert first.title == "Refactor parser"
    assert first.status == "In Progress"
    assert first.labels == ("bug", "P1")
    assert first.external_url == f"https://{DOMAIN}/browse/ACME-1"
    assert first.raw["issue_id"] == "10001"
    assert second.body == "ADF body"
    assert second.routing_hint.cli is None


@respx.mock
def test_pull_open_tickets_paginates() -> None:
    adapter = _make_adapter()
    page_one = _search_response(
        issues=[_issue(key="ACME-1"), _issue(key="ACME-2")],
        total=3,
    )
    page_two = _search_response(
        issues=[_issue(key="ACME-3")],
        total=3,
    )
    respx.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page_one),
            httpx.Response(200, json=page_two),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert [t.id for t in tickets] == ["ACME-1", "ACME-2", "ACME-3"]


@respx.mock
def test_pull_open_tickets_honours_jql_override() -> None:
    adapter = _make_adapter()
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response()),
    )
    try:
        list(adapter.pull_open_tickets({"jql": "labels in (urgent)"}))
    finally:
        adapter.close()
    sent_body = route.calls[0].request.content
    assert b'"jql":"labels in (urgent)"' in sent_body


@respx.mock
def test_pull_open_tickets_cli_routing_hint() -> None:
    adapter = _make_adapter(cli_field="customfield_10010")
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                issues=[
                    _issue(
                        key="ACME-1",
                        extra={"customfield_10010": {"value": "claude"}},
                    ),
                ],
            ),
        )
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets[0].routing_hint.cli == "claude"
    sent_body = route.calls[0].request.content
    assert b"customfield_10010" in sent_body


@respx.mock
def test_pull_open_tickets_handles_empty_page() -> None:
    adapter = _make_adapter()
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"issues": [], "total": 0}),
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets == []


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_posts_adf_payload() -> None:
    adapter = _make_adapter()
    comment_url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/comment"
    route = respx.post(comment_url).mock(
        return_value=httpx.Response(201, json={"id": "9001"}),
    )
    try:
        result = adapter.add_comment("ACME-1", "hello", idempotency_key="k1")
    finally:
        adapter.close()
    assert result.comment_id == "9001"
    assert result.ticket_id == "ACME-1"
    sent_body = route.calls[0].request.content
    assert b'"type":"doc"' in sent_body
    assert b"idempotency:k1" in sent_body
    assert b"hello" in sent_body


@respx.mock
def test_add_comment_without_idempotency_key() -> None:
    adapter = _make_adapter()
    comment_url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/comment"
    route = respx.post(comment_url).mock(
        return_value=httpx.Response(201, json={"id": "9002"}),
    )
    try:
        result = adapter.add_comment("ACME-1", "plain body")
    finally:
        adapter.close()
    assert result.comment_id == "9002"
    sent_body = route.calls[0].request.content
    assert b"idempotency:" not in sent_body
    assert b"plain body" in sent_body


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_posts_transition_id() -> None:
    adapter = _make_adapter()
    url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/transitions"
    route = respx.post(url).mock(return_value=httpx.Response(204))
    try:
        result = adapter.transition("ACME-1", "41")
    finally:
        adapter.close()
    assert result.new_status == "41"
    assert result.ticket_id == "ACME-1"
    sent_body = route.calls[0].request.content
    assert b'"id":"41"' in sent_body


@respx.mock
def test_transition_uses_status_map() -> None:
    adapter = _make_adapter(status_map={"done": "41"})
    url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/transitions"
    route = respx.post(url).mock(return_value=httpx.Response(204))
    try:
        result = adapter.transition("ACME-1", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"
    sent_body = route.calls[0].request.content
    assert b'"id":"41"' in sent_body


@respx.mock
def test_transition_propagates_idempotency_key_header() -> None:
    adapter = _make_adapter()
    url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/transitions"
    route = respx.post(url).mock(return_value=httpx.Response(204))
    try:
        adapter.transition("ACME-1", "41", idempotency_key="k1")
    finally:
        adapter.close()
    headers = route.calls[0].request.headers
    assert headers["X-Bernstein-Idempotency-Key"] == "k1"


# ---------------------------------------------------------------------------
# Rate-limit & concurrency
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            429,
            json={"message": "slow down"},
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
def test_conflict_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/transitions"
    respx.post(url).mock(return_value=httpx.Response(409, json={"message": "conflict"}))
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("ACME-1", "41")
    finally:
        adapter.close()


@respx.mock
def test_precondition_failed_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    url = f"https://{DOMAIN}/rest/api/3/issue/ACME-1/transitions"
    respx.post(url).mock(return_value=httpx.Response(412, json={"message": "precondition failed"}))
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("ACME-1", "41")
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(503, json={"message": "down"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_4xx_non_concurrency_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(401, json={"message": "auth"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()
