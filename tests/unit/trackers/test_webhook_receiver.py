"""Tests for :mod:`bernstein.core.trackers.webhook_receiver`."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.trackers import Ticket
from bernstein.core.trackers.webhook_receiver import (
    ReceiveResult,
    ReplayLedger,
    TrackerEvent,
    WebhookConfig,
    WebhookHandler,
    WebhookReceiver,
    get_handler,
    list_handlers,
    register_builtin_handlers,
    register_handler,
    replay_recent_via_poll,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _prefixed_sig(secret: str, body: bytes) -> str:
    return "sha256=" + _hex_sig(secret, body)


def _make_receiver(adapter: str, *, secret_env: str = "TEST_WH_SECRET") -> WebhookReceiver:
    receiver = WebhookReceiver()
    receiver.configure(adapter, WebhookConfig(enabled=True, secret_env=secret_env))
    return receiver


# ---------------------------------------------------------------------------
# ReplayLedger
# ---------------------------------------------------------------------------


def test_replay_ledger_dedupes_in_memory() -> None:
    ledger = ReplayLedger(max_entries=4)
    assert ledger.remember("a") is True
    assert ledger.remember("a") is False
    assert ledger.seen("a") is True
    assert ledger.seen("b") is False


def test_replay_ledger_persists_to_disk(tmp_path: Path) -> None:
    p = tmp_path / "ledger.jsonl"
    ledger1 = ReplayLedger(p)
    ledger1.remember("d-1")
    ledger1.remember("d-2")
    # Construct a second ledger pointing at the same file - replay should be
    # rejected even after a "restart".
    ledger2 = ReplayLedger(p)
    assert ledger2.seen("d-1") is True
    assert ledger2.remember("d-1") is False
    assert ledger2.remember("d-3") is True


def test_replay_ledger_evicts_oldest() -> None:
    ledger = ReplayLedger(max_entries=2)
    ledger.remember("x")
    ledger.remember("y")
    ledger.remember("z")
    # ``x`` should have been evicted now that ``z`` is in.
    assert ledger.seen("x") is False
    assert ledger.seen("y") is True
    assert ledger.seen("z") is True


def test_replay_ledger_disk_failure_does_not_raise(tmp_path: Path) -> None:
    # Use a path under a non-writable directory.  We simulate by pointing
    # at a child of a file (cannot be a directory).
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad_path = blocker / "ledger.jsonl"
    ledger = ReplayLedger(bad_path)
    # remember() must not raise even though mkdir + open will fail.
    assert ledger.remember("only-in-memory") is True
    assert ledger.seen("only-in-memory") is True


# ---------------------------------------------------------------------------
# Built-in handler registration
# ---------------------------------------------------------------------------


def test_builtin_handlers_registered() -> None:
    register_builtin_handlers()
    names = set(list_handlers())
    assert {"jira_cloud", "github", "gitlab", "linear", "plane"} <= names


# ---------------------------------------------------------------------------
# Receiver - verification & disabled paths
# ---------------------------------------------------------------------------


def test_receive_disabled_returns_disabled() -> None:
    receiver = WebhookReceiver()
    # No configure() call - adapter is disabled.
    result = receiver.receive("github", {}, b"{}")
    assert result.status == "disabled"


def test_receive_unknown_adapter() -> None:
    receiver = _make_receiver("nope")
    result = receiver.receive("nope", {}, b"{}")
    assert result.status == "unknown_adapter"


def test_receive_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_WH_SECRET", raising=False)
    receiver = _make_receiver("github")
    result = receiver.receive("github", {}, b"{}")
    assert result.status == "not_configured"


def test_receive_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "shh")
    receiver = _make_receiver("github")
    result = receiver.receive(
        "github",
        {"x-hub-signature-256": "sha256=" + "0" * 64},
        b"{}",
    )
    assert result.status == "bad_signature"


# ---------------------------------------------------------------------------
# GitHub handler
# ---------------------------------------------------------------------------


def _github_payload() -> dict[str, Any]:
    return {
        "action": "opened",
        "issue": {
            "id": 1,
            "number": 42,
            "html_url": "https://github.com/acme/repo/issues/42",
            "title": "Bug: parser crash",
            "body": "stack trace",
            "state": "open",
            "labels": [{"name": "bug"}, {"name": "p1"}],
        },
        "repository": {"full_name": "acme/repo"},
    }


def test_github_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "shh")
    receiver = _make_receiver("github")
    body = json.dumps(_github_payload()).encode("utf-8")
    headers = {
        "x-hub-signature-256": _prefixed_sig("shh", body),
        "x-github-event": "issues",
        "x-github-delivery": "deadbeef-1",
    }
    result = receiver.receive("github", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.adapter == "github"
    assert result.event.ticket.id == "acme/repo#42"
    assert result.event.ticket.labels == ("bug", "p1")
    assert result.event.delivery_id == "github:deadbeef-1"


def test_github_replay_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "shh")
    receiver = _make_receiver("github")
    body = json.dumps(_github_payload()).encode("utf-8")
    headers = {
        "x-hub-signature-256": _prefixed_sig("shh", body),
        "x-github-event": "issues",
        "x-github-delivery": "abc-replay",
    }
    first = receiver.receive("github", headers, body)
    second = receiver.receive("github", headers, body)
    assert first.status == "accepted"
    assert second.status == "replay"


def test_github_bad_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "shh")
    receiver = _make_receiver("github")
    body = b"not json"
    headers = {
        "x-hub-signature-256": _prefixed_sig("shh", body),
        "x-github-event": "issues",
        "x-github-delivery": "bad-1",
    }
    result = receiver.receive("github", headers, body)
    assert result.status == "bad_payload"


def test_github_ignores_unhandled_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "shh")
    receiver = _make_receiver("github")
    body = json.dumps({"action": "completed"}).encode("utf-8")
    headers = {
        "x-hub-signature-256": _prefixed_sig("shh", body),
        "x-github-event": "workflow_run",
        "x-github-delivery": "skip-1",
    }
    result = receiver.receive("github", headers, body)
    assert result.status == "ignored"


# ---------------------------------------------------------------------------
# Jira Cloud handler
# ---------------------------------------------------------------------------


def test_jira_cloud_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "jira-shh")
    receiver = _make_receiver("jira_cloud")
    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "id": "10042",
            "key": "ACME-7",
            "self": "https://acme.atlassian.net/rest/api/3/issue/10042",
            "fields": {
                "summary": "Refactor parser",
                "description": "details",
                "status": {"name": "In Progress"},
                "labels": ["backend", "p2"],
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-hub-signature-256": _prefixed_sig("jira-shh", body),
        "x-atlassian-webhook-identifier": "jira-1",
    }
    result = receiver.receive("jira_cloud", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.ticket.id == "ACME-7"
    assert result.event.ticket.external_url.startswith("https://acme.atlassian.net/browse/ACME-7")
    assert result.event.ticket.status == "In Progress"
    assert result.event.ticket.labels == ("backend", "p2")
    assert result.event.delivery_id == "jira_cloud:jira-1"


def test_jira_cloud_missing_issue_returns_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "jira-shh")
    receiver = _make_receiver("jira_cloud")
    body = json.dumps({"webhookEvent": "noop"}).encode("utf-8")
    headers = {
        "x-hub-signature-256": _prefixed_sig("jira-shh", body),
        "x-atlassian-webhook-identifier": "jira-empty",
    }
    result = receiver.receive("jira_cloud", headers, body)
    assert result.status == "ignored"


# ---------------------------------------------------------------------------
# GitLab handler
# ---------------------------------------------------------------------------


def test_gitlab_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "gl-token")
    receiver = _make_receiver("gitlab")
    payload = {
        "object_kind": "issue",
        "object_attributes": {
            "iid": 17,
            "title": "Race condition",
            "description": "see logs",
            "state": "opened",
            "url": "https://gitlab.example.com/acme/repo/-/issues/17",
            "action": "open",
        },
        "project": {"path_with_namespace": "acme/repo"},
        "labels": [{"title": "bug"}],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-gitlab-token": "gl-token",
        "x-gitlab-event-uuid": "gl-1",
    }
    result = receiver.receive("gitlab", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.ticket.id == "acme/repo#17"
    assert result.event.ticket.labels == ("bug",)
    assert result.event.delivery_id == "gitlab:gl-1"


def test_gitlab_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "gl-token")
    receiver = _make_receiver("gitlab")
    body = json.dumps({"object_kind": "issue"}).encode("utf-8")
    headers = {"x-gitlab-token": "wrong"}
    result = receiver.receive("gitlab", headers, body)
    assert result.status == "bad_signature"


# ---------------------------------------------------------------------------
# Linear handler
# ---------------------------------------------------------------------------


def test_linear_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "ln-secret")
    receiver = _make_receiver("linear")
    payload = {
        "type": "Issue",
        "action": "update",
        "data": {
            "id": "abc-uuid",
            "identifier": "ENG-12",
            "title": "Investigate flake",
            "description": "happens twice a week",
            "state": {"name": "In Progress"},
            "url": "https://linear.app/acme/issue/ENG-12",
            "labels": {"nodes": [{"name": "flake"}]},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "linear-signature": _hex_sig("ln-secret", body),
        "linear-delivery": "ln-1",
    }
    result = receiver.receive("linear", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.ticket.id == "ENG-12"
    assert result.event.ticket.labels == ("flake",)
    assert result.event.delivery_id == "linear:ln-1"


# ---------------------------------------------------------------------------
# Plane handler
# ---------------------------------------------------------------------------


def test_plane_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WH_SECRET", "plane-secret")
    receiver = _make_receiver("plane")
    payload = {
        "event": "issue.updated",
        "action": "updated",
        "data": {
            "id": "issue-uuid",
            "sequence_id": 3,
            "project": "proj-uuid",
            "name": "Telemetry stuck",
            "description_stripped": "details",
            "state": "In Progress",
            "url": "https://plane.example.com/.../issues/issue-uuid",
            "labels": ["telemetry"],
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-plane-signature": _hex_sig("plane-secret", body),
        "x-plane-delivery": "plane-1",
    }
    result = receiver.receive("plane", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.ticket.id == "proj-uuid#3"
    assert result.event.ticket.status == "In Progress"
    assert result.event.delivery_id == "plane:plane-1"


# ---------------------------------------------------------------------------
# Custom handler registration
# ---------------------------------------------------------------------------


def test_register_custom_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    def _verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
        del body
        return headers.get("x-test-token") == secret

    def _parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
        del headers
        if not payload.get("id"):
            return None
        return TrackerEvent(
            adapter="custom",
            action=str(payload.get("action") or "updated"),
            ticket=Ticket(
                id=str(payload["id"]),
                external_url="",
                title=str(payload.get("title") or ""),
                body="",
                status="open",
            ),
            delivery_id="",
        )

    def _delivery(headers: dict[str, str], body: bytes) -> str:
        del body
        return f"custom:{headers.get('x-test-delivery', 'unknown')}"

    register_handler(
        WebhookHandler(
            adapter="custom_test_tracker",
            verify_signature=_verify,
            parse_event=_parse,
            delivery_id=_delivery,
        )
    )
    assert get_handler("custom_test_tracker") is not None

    monkeypatch.setenv("CUSTOM_WH", "abc")
    receiver = _make_receiver("custom_test_tracker", secret_env="CUSTOM_WH")
    headers = {"x-test-token": "abc", "x-test-delivery": "d-1"}
    body = json.dumps({"id": "t-1", "action": "created"}).encode("utf-8")
    result = receiver.receive("custom_test_tracker", headers, body)
    assert result.status == "accepted"
    assert result.event is not None
    assert result.event.delivery_id == "custom:d-1"


# ---------------------------------------------------------------------------
# Startup-poll recovery
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def __init__(self, tickets: list[Ticket]) -> None:
        self._tickets = tickets

    def pull_open_tickets(self) -> Any:
        return iter(self._tickets)


def test_replay_recent_via_poll_filters_by_timestamp() -> None:
    old = Ticket(
        id="OLD-1",
        external_url="",
        title="old",
        body="",
        status="open",
        raw={"updated_at": 100.0},
    )
    fresh = Ticket(
        id="NEW-1",
        external_url="",
        title="fresh",
        body="",
        status="open",
        raw={"updated_at": 999.0},
    )
    delivered: list[Ticket] = []
    n = replay_recent_via_poll(
        _FakeAdapter([old, fresh]),
        last_processed_ts=500.0,
        sink=delivered.append,
    )
    assert n == 1
    assert delivered[0].id == "NEW-1"


def test_replay_recent_via_poll_no_timestamps_replays_all() -> None:
    tickets = [
        Ticket(id="A", external_url="", title="a", body="", status="open"),
        Ticket(id="B", external_url="", title="b", body="", status="open"),
    ]
    delivered: list[Ticket] = []
    n = replay_recent_via_poll(_FakeAdapter(tickets), last_processed_ts=0.0, sink=delivered.append)
    assert n == 2


def test_replay_recent_via_poll_adapter_without_pull_returns_zero() -> None:
    class _NoPull:
        pass

    n = replay_recent_via_poll(_NoPull(), last_processed_ts=0.0, sink=lambda t: None)
    assert n == 0


# ---------------------------------------------------------------------------
# Receive result helper
# ---------------------------------------------------------------------------


def test_receive_result_defaults() -> None:
    r = ReceiveResult(status="accepted")
    assert r.delivery_id is None
    assert r.event is None
