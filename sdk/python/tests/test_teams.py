"""Tests for bernstein_sdk.adapters.teams."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from bernstein_sdk.adapters.teams import (
    TeamsAdapter,
    _adaptive_card,
    _completed_card,
    _created_card,
    _failed_card,
    _text_card,
)


class TestTeamsAdapter:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.webhook.office.com/test")
        adapter = TeamsAdapter.from_env()
        assert adapter._webhook_url == "https://teams.webhook.office.com/test"

    def test_no_webhook_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="bernstein_sdk.adapters.teams"):
            TeamsAdapter()
        assert any("no webhook URL configured" in r.message for r in caplog.records)

    def test_notify_task_completed_fires(self) -> None:
        adapter = TeamsAdapter(webhook_url="https://teams.webhook.office.com/test")
        with patch("bernstein_sdk.adapters.teams.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            adapter.notify_task_completed("t1", "Fix bug", "backend", "done")
            import time

            time.sleep(0.1)
        mock_open.assert_called_once()

    def test_notify_task_failed_fires(self) -> None:
        adapter = TeamsAdapter(webhook_url="https://teams.webhook.office.com/test")
        with patch("bernstein_sdk.adapters.teams.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            adapter.notify_task_failed("t2", "Crash", "qa", "OOM")
            import time

            time.sleep(0.1)
        mock_open.assert_called_once()

    def test_notify_task_created_fires(self) -> None:
        adapter = TeamsAdapter(webhook_url="https://teams.webhook.office.com/test")
        with patch("bernstein_sdk.adapters.teams.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            adapter.notify_task_created("t3", "New task", "backend", priority=1)
            import time

            time.sleep(0.1)
        mock_open.assert_called_once()

    def test_post_message_fires(self) -> None:
        adapter = TeamsAdapter(webhook_url="https://teams.webhook.office.com/test")
        with patch("bernstein_sdk.adapters.teams.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            adapter.post_message("Hello from Bernstein")
            import time

            time.sleep(0.1)
        mock_open.assert_called_once()

    def test_no_webhook_skips_post(self) -> None:
        adapter = TeamsAdapter.__new__(TeamsAdapter)
        adapter._webhook_url = ""
        with patch("bernstein_sdk.adapters.teams.urllib.request.urlopen") as mock_open:
            adapter._post_async({"type": "message"})
            import time

            time.sleep(0.05)
        mock_open.assert_not_called()

    def test_exception_is_caught(self) -> None:
        adapter = TeamsAdapter(webhook_url="https://teams.webhook.office.com/test")
        with patch(
            "bernstein_sdk.adapters.teams.urllib.request.urlopen",
            side_effect=OSError("refused"),
        ):
            # Should not raise — exception is caught in daemon thread
            adapter.notify_task_completed("t1", "Title", "backend")
            import time

            time.sleep(0.1)


class TestAdaptiveCards:
    def _get_card_content(self, card: dict) -> dict:  # type: ignore[type-arg]
        return card["attachments"][0]["content"]

    def test_completed_card_structure(self) -> None:
        card = _completed_card("t1", "Fix crash", "backend", "Patched auth.py")
        content = self._get_card_content(card)
        assert content["type"] == "AdaptiveCard"
        assert content["version"] == "1.4"
        body = content["body"]
        # Title block
        assert body[0]["text"] == "✅ Task Completed"
        assert body[0]["color"] == "Good"
        # Subtitle
        assert body[1]["text"] == "Fix crash"
        # FactSet has task id and result
        fact_titles = [f["title"] for f in body[2]["facts"]]
        assert "Task ID" in fact_titles
        assert "Result" in fact_titles

    def test_completed_card_no_summary(self) -> None:
        card = _completed_card("t1", "Fix crash", "backend", "")
        content = self._get_card_content(card)
        fact_titles = [f["title"] for f in content["body"][2]["facts"]]
        assert "Result" not in fact_titles

    def test_failed_card_structure(self) -> None:
        card = _failed_card("t2", "Crash", "qa", "OOM error")
        content = self._get_card_content(card)
        assert content["body"][0]["text"] == "❌ Task Failed"
        assert content["body"][0]["color"] == "Attention"
        fact_titles = [f["title"] for f in content["body"][2]["facts"]]
        assert "Error" in fact_titles

    def test_failed_card_no_error(self) -> None:
        card = _failed_card("t2", "Crash", "qa", "")
        content = self._get_card_content(card)
        fact_titles = [f["title"] for f in content["body"][2]["facts"]]
        assert "Error" not in fact_titles

    def test_created_card_priority_label(self) -> None:
        for priority, label in [(1, "Critical"), (2, "Normal"), (3, "Low")]:
            card = _created_card("t3", "New task", "backend", priority)
            content = self._get_card_content(card)
            facts = {f["title"]: f["value"] for f in content["body"][2]["facts"]}
            assert facts["Priority"] == label

    def test_text_card_structure(self) -> None:
        card = _text_card("Hello from Bernstein")
        content = self._get_card_content(card)
        assert content["body"][0]["text"] == "Hello from Bernstein"
        assert content["body"][0]["type"] == "TextBlock"

    def test_adaptive_card_envelope(self) -> None:
        card = _adaptive_card(
            "Title", "Subtitle", "Good", [{"title": "k", "value": "v"}]
        )
        assert card["type"] == "message"
        attachment = card["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
