"""Tests for the Manus-style harness-policy data model."""

from __future__ import annotations

import pytest

from bernstein.core.agents.harness_policy import (
    ALL_ON_POLICY,
    DEFAULT_POLICY,
    HarnessPolicy,
)


class TestHarnessPolicyDefaults:
    def test_default_policy_is_all_off(self) -> None:
        """All five flags must default to False so existing behaviour is preserved."""
        assert HarnessPolicy() == DEFAULT_POLICY
        assert DEFAULT_POLICY.kv_cache_locality is False
        assert DEFAULT_POLICY.tool_masking is False
        assert DEFAULT_POLICY.filesystem_memory is False
        assert DEFAULT_POLICY.todo_recitation is False
        assert DEFAULT_POLICY.keep_failed_actions is False

    def test_all_on_policy_enables_every_flag(self) -> None:
        assert ALL_ON_POLICY.kv_cache_locality is True
        assert ALL_ON_POLICY.tool_masking is True
        assert ALL_ON_POLICY.filesystem_memory is True
        assert ALL_ON_POLICY.todo_recitation is True
        assert ALL_ON_POLICY.keep_failed_actions is True

    def test_policy_is_frozen_dataclass(self) -> None:
        """HarnessPolicy must be immutable to prevent accidental mid-spawn mutation."""
        p = HarnessPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.tool_masking = True  # type: ignore[misc]


class TestWithOverrides:
    def test_single_flag_override_returns_new_instance(self) -> None:
        base = HarnessPolicy()
        new = base.with_overrides(tool_masking=True)
        assert base is not new
        assert base.tool_masking is False
        assert new.tool_masking is True
        # Other flags unchanged
        assert new.kv_cache_locality is False

    def test_multi_flag_override(self) -> None:
        new = HarnessPolicy().with_overrides(
            tool_masking=True,
            keep_failed_actions=True,
        )
        assert new.tool_masking is True
        assert new.keep_failed_actions is True
        assert new.filesystem_memory is False

    def test_override_with_unknown_flag_raises(self) -> None:
        with pytest.raises(TypeError):
            HarnessPolicy().with_overrides(does_not_exist=True)  # type: ignore[call-arg]

    def test_override_preserves_equality_when_no_change(self) -> None:
        base = HarnessPolicy(tool_masking=True)
        same = base.with_overrides(tool_masking=True)
        assert base == same
