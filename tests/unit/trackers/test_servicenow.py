"""Tests for :mod:`bernstein.core.trackers.servicenow`."""

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
from bernstein.core.trackers.servicenow import (
    ServiceNowConfig,
    ServiceNowTracker,
    _resolve_credentials,
    _resolve_instance_url,
)

INSTANCE_URL = "https://dev12345.service-now.com"
TABLE_URL = f"{INSTANCE_URL}/api/now/table/incident"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(
    *,
    sys_id: str = "abc123",
    number: str = "INC0010001",
    short_description: str = "Disk full on web-01",
    description: str = "Root volume at 99%.",
    state_value: str = "1",
    state_display: str = "New",
) -> dict[str, Any]:
    return {
        "sys_id": {"value": sys_id, "display_value": sys_id},
        "number": {"value": number, "display_value": number},
        "short_description": {
            "value": short_description,
            "display_value": short_description,
        },
        "description": {"value": description, "display_value": description},
        "state": {"value": state_value, "display_value": state_display},
    }


def _make_adapter(
    *,
    state_map: dict[str, str] | None = None,
    table_name: str = "incident",
    state_field: str = "state",
) -> ServiceNowTracker:
    config = ServiceNowConfig(
        instance_url=INSTANCE_URL,
        table_name=table_name,
        state_field=state_field,
        state_map=state_map or {},
    )
    return ServiceNowTracker(
        config=config,
        credential_provider=lambda: ("admin", "secret"),
        instance_url_provider=lambda: INSTANCE_URL,
    )


# ---------------------------------------------------------------------------
# Credential / instance-URL resolution
# ---------------------------------------------------------------------------


def test_resolve_instance_url_from_config() -> None:
    config = ServiceNowConfig(instance_url=INSTANCE_URL + "/")
    assert _resolve_instance_url(config) == INSTANCE_URL


def test_resolve_instance_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", INSTANCE_URL)
    config = ServiceNowConfig()
    assert _resolve_instance_url(config) == INSTANCE_URL


def test_resolve_instance_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)
    config = ServiceNowConfig()
    with pytest.raises(TrackerUnavailable):
        _resolve_instance_url(config)


def test_resolve_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICENOW_USERNAME", "user")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "pass")
    config = ServiceNowConfig()
    assert _resolve_credentials(config) == ("user", "pass")


def test_resolve_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)
    monkeypatch.delenv("SERVICENOW_PASSWORD", raising=False)
    config = ServiceNowConfig()
    with pytest.raises(TrackerUnavailable):
        _resolve_credentials(config)


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


@respx.mock
def test_pull_open_tickets_emits_normalised_tickets() -> None:
    adapter = _make_adapter()
    route = respx.get(TABLE_URL).mock(return_value=httpx.Response(200, json={"result": [_record()]}))
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert route.call_count == 1
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.id == "abc123"
    assert ticket.title == "Disk full on web-01"
    assert ticket.body == "Root volume at 99%."
    assert ticket.status == "New"
    assert ticket.raw["number"] == "INC0010001"
    assert ticket.raw["table"] == "incident"
    assert "sys_id=abc123" in ticket.external_url
    # Default open query is applied.
    call = route.calls[0].request
    assert b"sysparm_query=active%3Dtrue" in call.url.query


@respx.mock
def test_pull_open_tickets_respects_filter_override() -> None:
    adapter = _make_adapter()
    route = respx.get(TABLE_URL).mock(return_value=httpx.Response(200, json={"result": [_record()]}))
    try:
        list(adapter.pull_open_tickets({"sysparm_query": "active=true^priority=1", "fields": "sys_id,number"}))
    finally:
        adapter.close()
    call = route.calls[0].request
    query_str = call.url.query.decode()
    assert "sysparm_query=active%3Dtrue%5Epriority%3D1" in query_str
    assert "sysparm_fields=sys_id%2Cnumber" in query_str


@respx.mock
def test_pull_open_tickets_paginates_until_short_page() -> None:
    adapter = _make_adapter()
    config = adapter._config
    full_page = {"result": [_record(sys_id=f"id{i}") for i in range(config.page_size)]}
    short_page = {"result": [_record(sys_id="tail")]}
    route = respx.get(TABLE_URL).mock(
        side_effect=[
            httpx.Response(200, json=full_page),
            httpx.Response(200, json=short_page),
        ]
    )
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert route.call_count == 2
    assert len(tickets) == config.page_size + 1
    assert tickets[-1].id == "tail"


@respx.mock
def test_pull_open_tickets_handles_empty_result() -> None:
    adapter = _make_adapter()
    respx.get(TABLE_URL).mock(return_value=httpx.Response(200, json={"result": []}))
    try:
        tickets = list(adapter.pull_open_tickets())
    finally:
        adapter.close()
    assert tickets == []


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@respx.mock
def test_add_comment_appends_to_work_notes() -> None:
    adapter = _make_adapter()
    record_url = f"{TABLE_URL}/abc123"
    route = respx.patch(record_url).mock(return_value=httpx.Response(200, json={"result": {"sys_id": "abc123"}}))
    try:
        result = adapter.add_comment("abc123", "Hello there", idempotency_key="k1")
    finally:
        adapter.close()
    assert result.comment_id == "abc123"
    assert result.ticket_id == "abc123"
    sent = route.calls[0].request.content
    assert b'"work_notes"' in sent
    assert b"Hello there" in sent
    assert b"idempotency:k1" in sent


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@respx.mock
def test_transition_passes_status_directly() -> None:
    adapter = _make_adapter()
    record_url = f"{TABLE_URL}/abc123"
    route = respx.patch(record_url).mock(return_value=httpx.Response(200, json={"result": {"sys_id": "abc123"}}))
    try:
        result = adapter.transition("abc123", "6")
    finally:
        adapter.close()
    assert result.ticket_id == "abc123"
    assert result.new_status == "6"
    sent = route.calls[0].request.content
    assert b'"state":"6"' in sent


@respx.mock
def test_transition_uses_state_map() -> None:
    adapter = _make_adapter(state_map={"done": "6"})
    record_url = f"{TABLE_URL}/abc123"
    route = respx.patch(record_url).mock(return_value=httpx.Response(200, json={"result": {"sys_id": "abc123"}}))
    try:
        result = adapter.transition("abc123", "done")
    finally:
        adapter.close()
    assert result.new_status == "done"
    sent = route.calls[0].request.content
    assert b'"state":"6"' in sent


@respx.mock
def test_transition_supports_custom_table_and_state_field() -> None:
    adapter = _make_adapter(table_name="change_request", state_field="approval")
    record_url = f"{INSTANCE_URL}/api/now/table/change_request/abc123"
    route = respx.patch(record_url).mock(return_value=httpx.Response(200, json={"result": {"sys_id": "abc123"}}))
    try:
        adapter.transition("abc123", "approved")
    finally:
        adapter.close()
    sent = route.calls[0].request.content
    assert b'"approval":"approved"' in sent


# ---------------------------------------------------------------------------
# Rate limit / concurrency / errors
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_with_retry_after_header() -> None:
    adapter = _make_adapter()
    respx.get(TABLE_URL).mock(
        return_value=httpx.Response(
            429,
            json={"error": "slow down"},
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
def test_etag_mismatch_raises_optimistic_concurrency() -> None:
    adapter = _make_adapter()
    record_url = f"{TABLE_URL}/abc123"
    respx.patch(record_url).mock(return_value=httpx.Response(412, json={"error": "etag"}))
    try:
        with pytest.raises(OptimisticConcurrencyError):
            adapter.transition("abc123", "6", etag='W/"abc"')
    finally:
        adapter.close()


@respx.mock
def test_5xx_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(TABLE_URL).mock(return_value=httpx.Response(503, json={"error": "down"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


@respx.mock
def test_401_raises_tracker_unavailable() -> None:
    adapter = _make_adapter()
    respx.get(TABLE_URL).mock(return_value=httpx.Response(401, json={"error": "auth"}))
    try:
        with pytest.raises(TrackerUnavailable):
            list(adapter.pull_open_tickets())
    finally:
        adapter.close()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_factory_returns_servicenow_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.trackers import get_tracker

    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", INSTANCE_URL)
    monkeypatch.setenv("SERVICENOW_USERNAME", "u")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "p")
    adapter = get_tracker("servicenow", config=ServiceNowConfig(instance_url=INSTANCE_URL))
    try:
        assert isinstance(adapter, ServiceNowTracker)
        assert adapter.name == "servicenow"
    finally:
        adapter.close()


def test_factory_unknown_name_raises() -> None:
    from bernstein.core.trackers import get_tracker

    with pytest.raises(ValueError, match="Unknown tracker"):
        get_tracker("does_not_exist")
