"""Unit tests for ``bernstein.gitlab_app.webhooks``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bernstein.gitlab_app.webhooks import (
    GitLabWebhookEvent,
    parse_webhook,
    verify_token,
)


class TestVerifyToken:
    """Constant-time token verification."""

    def test_match(self) -> None:
        assert verify_token("abc123", "abc123") is True

    def test_mismatch(self) -> None:
        assert verify_token("abc123", "xyz") is False

    def test_empty_provided(self) -> None:
        assert verify_token("", "expected") is False

    def test_empty_expected(self) -> None:
        assert verify_token("provided", "") is False

    def test_both_empty(self) -> None:
        assert verify_token("", "") is False

    def test_length_mismatch(self) -> None:
        assert verify_token("a", "abcdef") is False

    def test_case_sensitive(self) -> None:
        assert verify_token("ABC", "abc") is False

    def test_unicode(self) -> None:
        assert verify_token("ééé", "ééé") is True

    def test_uses_constant_time_compare(self) -> None:
        """Smoke-test: function should use hmac.compare_digest.

        We verify by patching the helper and asserting it is called.
        """
        import bernstein.gitlab_app.webhooks as webhooks_mod

        calls: list[tuple[bytes, bytes]] = []
        original = webhooks_mod.hmac.compare_digest

        def _spy(a: bytes, b: bytes) -> bool:
            calls.append((a, b))
            return original(a, b)

        webhooks_mod.hmac.compare_digest = _spy  # type: ignore[assignment]
        try:
            verify_token("x", "x")
        finally:
            webhooks_mod.hmac.compare_digest = original  # type: ignore[assignment]
        assert calls == [(b"x", b"x")]


def _mr_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "object_kind": "merge_request",
        "object_attributes": {
            "iid": 7,
            "title": "Add a feature",
            "description": "Body of the MR",
            "action": "open",
            "labels": [{"title": "backend"}],
        },
        "project": {"path_with_namespace": "acme/widgets", "id": 42},
        "user": {"username": "alice", "name": "Alice"},
    }
    for k, v in overrides.items():
        payload[k] = v
    return payload


def _pipeline_payload(status: str = "failed") -> dict[str, Any]:
    return {
        "object_kind": "pipeline",
        "object_attributes": {
            "id": 999,
            "sha": "abcdef1234567890",
            "ref": "feature-x",
            "status": status,
            "url": "https://gitlab.com/acme/widgets/-/pipelines/999",
        },
        "project": {"path_with_namespace": "acme/widgets", "id": 42},
        "user": {"username": "bob"},
        "builds": [
            {"id": 1001, "name": "lint", "stage": "quality", "status": "failed"},
            {"id": 1002, "name": "test", "stage": "test", "status": "passed"},
        ],
    }


def _note_payload(note: str, noteable_type: str = "MergeRequest") -> dict[str, Any]:
    return {
        "object_kind": "note",
        "object_attributes": {
            "note": note,
            "noteable_type": noteable_type,
        },
        "merge_request": {
            "iid": 8,
            "title": "MR with note",
        },
        "project": {"path_with_namespace": "acme/widgets", "id": 42},
        "user": {"username": "carol"},
    }


class TestParseWebhook:
    """``parse_webhook`` happy-path and error handling."""

    def test_mr_open(self) -> None:
        body = json.dumps(_mr_payload()).encode("utf-8")
        evt = parse_webhook({"X-Gitlab-Event": "Merge Request Hook"}, body)
        assert isinstance(evt, GitLabWebhookEvent)
        assert evt.event_type == "Merge Request Hook"
        assert evt.object_kind == "merge_request"
        assert evt.action == "open"
        assert evt.project_path == "acme/widgets"
        assert evt.sender == "alice"

    def test_pipeline(self) -> None:
        body = json.dumps(_pipeline_payload()).encode("utf-8")
        evt = parse_webhook({"x-gitlab-event": "Pipeline Hook"}, body)
        assert evt.object_kind == "pipeline"
        assert evt.action == ""  # pipelines have no .action sub-field
        assert evt.sender == "bob"

    def test_note(self) -> None:
        body = json.dumps(_note_payload("hello")).encode("utf-8")
        evt = parse_webhook({"x-gitlab-event": "Note Hook"}, body)
        assert evt.object_kind == "note"
        assert evt.sender == "carol"

    def test_header_case_insensitive(self) -> None:
        body = json.dumps(_mr_payload()).encode("utf-8")
        evt = parse_webhook({"X-GITLAB-EVENT": "Merge Request Hook"}, body)
        assert evt.event_type == "Merge Request Hook"

    def test_missing_event_header(self) -> None:
        body = json.dumps(_mr_payload()).encode("utf-8")
        with pytest.raises(ValueError, match="X-Gitlab-Event"):
            parse_webhook({}, body)

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_webhook({"X-Gitlab-Event": "x"}, b"{not json")

    def test_non_object_json_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_webhook({"X-Gitlab-Event": "x"}, b"[1, 2, 3]")

    def test_missing_project_path(self) -> None:
        payload = _mr_payload()
        payload["project"] = {}
        with pytest.raises(ValueError, match="project.path_with_namespace"):
            parse_webhook({"X-Gitlab-Event": "x"}, json.dumps(payload).encode("utf-8"))

    def test_falls_back_to_project_name(self) -> None:
        payload = _mr_payload()
        payload["project"] = {"name": "only-name"}
        evt = parse_webhook({"X-Gitlab-Event": "x"}, json.dumps(payload).encode("utf-8"))
        assert evt.project_path == "only-name"

    def test_unknown_user_defaults(self) -> None:
        payload = _mr_payload()
        payload["user"] = {}
        evt = parse_webhook({"X-Gitlab-Event": "x"}, json.dumps(payload).encode("utf-8"))
        assert evt.sender == "unknown"

    def test_user_name_fallback(self) -> None:
        payload = _mr_payload()
        payload["user"] = {"name": "Bob"}
        evt = parse_webhook({"X-Gitlab-Event": "x"}, json.dumps(payload).encode("utf-8"))
        assert evt.sender == "Bob"

    def test_object_attributes_not_dict(self) -> None:
        payload = _mr_payload()
        payload["object_attributes"] = "not-a-dict"
        evt = parse_webhook({"X-Gitlab-Event": "x"}, json.dumps(payload).encode("utf-8"))
        # Should not raise; action just defaults to empty.
        assert evt.action == ""

    def test_invalid_utf8(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_webhook({"X-Gitlab-Event": "x"}, b"\xff\xfe\xfa")

    def test_event_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        body = json.dumps(_mr_payload()).encode("utf-8")
        evt = parse_webhook({"X-Gitlab-Event": "x"}, body)
        with pytest.raises(FrozenInstanceError):
            evt.sender = "other"  # type: ignore[misc]
