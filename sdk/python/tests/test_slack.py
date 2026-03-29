"""Tests for bernstein_sdk.adapters.slack."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bernstein_sdk.adapters.slack import (
    SlackAdapter,
    _blocks_fallback,
    _task_completed_blocks,
    _task_created_blocks,
    _task_failed_blocks,
)


class TestSlackAdapter:
    def test_from_env_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        adapter = SlackAdapter.from_env()
        assert adapter._webhook_url == "https://hooks.slack.com/test"

    def test_from_env_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_CHANNEL", "#eng")
        adapter = SlackAdapter.from_env()
        assert adapter._bot_token == "xoxb-test"
        assert adapter._channel == "#eng"

    def test_from_env_mention_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        monkeypatch.setenv("SLACK_MENTION_ON_FAILURE", "<!here>")
        adapter = SlackAdapter.from_env()
        assert adapter._mention_on_failure == "<!here>"

    def test_no_config_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        adapter = SlackAdapter()
        with caplog.at_level(logging.WARNING, logger="bernstein_sdk.adapters.slack"):
            adapter.notify_task_completed("t1", "Title", "backend")
        # Warning should be logged since no webhook or token
        assert any("no webhook URL or bot token" in r.message for r in caplog.records)

    def test_notify_task_completed_fires_thread(self) -> None:
        adapter = SlackAdapter(webhook_url="https://hooks.slack.com/test")
        with patch("bernstein_sdk.adapters.slack._post_webhook") as mock_post:
            adapter.notify_task_completed(
                "t1", "Fix login bug", "backend", "Patched auth.py"
            )
            # Wait briefly for daemon thread
            import time

            time.sleep(0.1)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://hooks.slack.com/test"

    def test_notify_task_failed_fires_thread(self) -> None:
        adapter = SlackAdapter(webhook_url="https://hooks.slack.com/test")
        with patch("bernstein_sdk.adapters.slack._post_webhook") as mock_post:
            adapter.notify_task_failed("t2", "Broken pipe", "qa", error="OOM")
            import time

            time.sleep(0.1)
        mock_post.assert_called_once()

    def test_notify_task_created_fires_thread(self) -> None:
        adapter = SlackAdapter(webhook_url="https://hooks.slack.com/test")
        with patch("bernstein_sdk.adapters.slack._post_webhook") as mock_post:
            adapter.notify_task_created("t3", "New feature", "backend", priority=1)
            import time

            time.sleep(0.1)
        mock_post.assert_called_once()

    def test_bot_token_path_uses_web_api(self) -> None:
        adapter = SlackAdapter(bot_token="xoxb-test", channel="#dev")
        with patch("bernstein_sdk.adapters.slack._post_web_api") as mock_api:
            adapter.post_message("Hello from Bernstein")
            import time

            time.sleep(0.1)
        mock_api.assert_called_once()

    def test_bot_token_no_channel_skips(self, caplog: pytest.LogCaptureFixture) -> None:
        adapter = SlackAdapter(bot_token="xoxb-test")
        with patch("bernstein_sdk.adapters.slack._post_web_api") as mock_api:
            adapter.post_message("Hello")
            import time

            time.sleep(0.1)
        mock_api.assert_not_called()


class TestBlockBuilders:
    def test_task_completed_blocks_basic(self) -> None:
        blocks = _task_completed_blocks("t1", "Fix bug", "backend", "")
        assert len(blocks) == 2
        text = blocks[0]["text"]["text"]
        assert "t1" in text
        assert "completed" in text.lower()

    def test_task_completed_blocks_with_summary(self) -> None:
        blocks = _task_completed_blocks("t1", "Fix bug", "backend", "summary text")
        # fields block should include result
        fields = blocks[1]["fields"]
        result_field = next(f for f in fields if "Result" in f["text"])
        assert "summary text" in result_field["text"]

    def test_task_failed_blocks_with_mention(self) -> None:
        blocks = _task_failed_blocks("t2", "Crash", "qa", "OOM", "<!here>")
        header = blocks[0]["text"]["text"]
        assert "<!here>" in header
        assert "failed" in header.lower()

    def test_task_failed_blocks_with_error(self) -> None:
        blocks = _task_failed_blocks("t2", "Crash", "qa", "stack overflow", "")
        assert len(blocks) == 3
        error_block = blocks[2]["text"]["text"]
        assert "stack overflow" in error_block

    def test_task_failed_blocks_no_error(self) -> None:
        blocks = _task_failed_blocks("t3", "Crash", "qa", "", "")
        assert len(blocks) == 2

    def test_task_created_blocks_priority_emojis(self) -> None:
        for priority in [1, 2, 3]:
            blocks = _task_created_blocks("t1", "New task", "backend", priority)
            assert len(blocks) == 1
            text = blocks[0]["text"]["text"]
            assert "t1" in text

    def test_blocks_fallback_extracts_text(self) -> None:
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Hello world"}}
        ]
        assert _blocks_fallback(blocks) == "Hello world"

    def test_blocks_fallback_empty(self) -> None:
        assert _blocks_fallback([]) == "Bernstein notification"

    def test_blocks_fallback_no_text_key(self) -> None:
        blocks = [{"type": "divider"}]
        assert _blocks_fallback(blocks) == "Bernstein notification"
