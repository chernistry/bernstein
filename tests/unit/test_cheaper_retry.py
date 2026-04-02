"""Tests for cheaper retry logic."""

from __future__ import annotations

from bernstein.core.cheaper_retry import (
    apply_cheaper_retry,
    get_retry_model,
    should_use_cheaper_retry,
)


class TestCheaperRetry:
    """Test cheaper retry logic."""

    def test_get_retry_model_opus(self) -> None:
        """Test opus → sonnet downgrade."""
        model, effort = get_retry_model("opus", "max")

        assert model == "sonnet"
        assert effort == "high"

    def test_get_retry_model_sonnet(self) -> None:
        """Test sonnet → haiku downgrade."""
        model, effort = get_retry_model("sonnet", "high")

        assert model == "haiku"
        assert effort == "medium"

    def test_get_retry_model_unknown(self) -> None:
        """Test unknown model stays same."""
        model, effort = get_retry_model("unknown", "low")

        assert model == "unknown"
        assert effort == "low"

    def test_should_use_cheaper_retry_first_retry(self) -> None:
        """Test cheaper retry on first retry."""
        task_data = {"model": "opus", "effort": "max"}

        result = should_use_cheaper_retry(task_data, retry_count=1)

        assert result is True

    def test_should_not_use_cheaper_retry_later_retry(self) -> None:
        """Test no cheaper retry on later retries."""
        task_data = {"model": "opus", "effort": "max"}

        result = should_use_cheaper_retry(task_data, retry_count=2)

        assert result is False

    def test_should_not_use_cheaper_retry_already_cheap(self) -> None:
        """Test no cheaper retry if already cheap model."""
        task_data = {"model": "haiku", "effort": "low"}

        result = should_use_cheaper_retry(task_data, retry_count=1)

        assert result is False

    def test_apply_cheaper_retry(self) -> None:
        """Test applying cheaper retry."""
        task_data = {
            "id": "task-123",
            "model": "opus",
            "effort": "max",
        }

        result = apply_cheaper_retry(task_data)

        assert result["model"] == "sonnet"
        assert result["effort"] == "high"
        assert result["id"] == "task-123"
