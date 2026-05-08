"""Unit tests for :class:`bernstein.core.models.ApprovalSpec` (#1110)."""

from __future__ import annotations

import pytest
from bernstein.core.models import ApprovalSpec, Task


class TestApprovalSpecValidation:
    """Field invariants enforced at construction time."""

    def test_default_timeout_is_24h(self) -> None:
        spec = ApprovalSpec(prompt="ship?")
        assert spec.timeout_seconds == 86_400

    def test_default_action_is_reject(self) -> None:
        spec = ApprovalSpec(prompt="ship?")
        assert spec.default_action == "reject"

    def test_blank_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ApprovalSpec(prompt="")

    def test_whitespace_only_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ApprovalSpec(prompt="   \t\n")

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match=">"):
            ApprovalSpec(prompt="ok", timeout_seconds=0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match=">"):
            ApprovalSpec(prompt="ok", timeout_seconds=-1)

    def test_explicit_approve_default(self) -> None:
        spec = ApprovalSpec(prompt="ok", default_action="approve")
        assert spec.default_action == "approve"

    def test_explicit_fail_default(self) -> None:
        spec = ApprovalSpec(prompt="ok", default_action="fail")
        assert spec.default_action == "fail"


class TestApprovalSpecSerialisation:
    """Round-trip via :meth:`to_dict` / :meth:`from_dict`."""

    def test_round_trip_preserves_fields(self) -> None:
        original = ApprovalSpec(
            prompt="Promote to prod?",
            timeout_seconds=600,
            default_action="approve",
        )
        dumped = original.to_dict()
        rebuilt = ApprovalSpec.from_dict(dumped)
        assert rebuilt == original

    def test_from_dict_uses_defaults_for_missing_keys(self) -> None:
        spec = ApprovalSpec.from_dict({"prompt": "go?"})
        assert spec.prompt == "go?"
        assert spec.timeout_seconds == 86_400
        assert spec.default_action == "reject"

    def test_from_dict_rejects_unknown_default_action(self) -> None:
        with pytest.raises(ValueError):
            ApprovalSpec.from_dict({"prompt": "go?", "default_action": "explode"})

    def test_from_dict_propagates_validation_errors(self) -> None:
        with pytest.raises(ValueError):
            ApprovalSpec.from_dict({"prompt": "", "timeout_seconds": 60})


class TestTaskApprovalSpecField:
    """Wiring of the new optional ``Task.approval_spec`` field."""

    def test_task_default_has_no_approval_spec(self) -> None:
        task = Task(id="T-1", title="t", description="d", role="backend")
        assert task.approval_spec is None
        # Legacy boolean still defaults False so existing tests stay green.
        assert task.approval_required is False

    def test_task_accepts_approval_spec(self) -> None:
        spec = ApprovalSpec(prompt="ok?")
        task = Task(
            id="T-1",
            title="t",
            description="d",
            role="backend",
            approval_spec=spec,
        )
        assert task.approval_spec is spec

    def test_from_dict_round_trips_approval_spec(self) -> None:
        raw = {
            "id": "T-2",
            "title": "ship",
            "description": "ship the thing",
            "role": "backend",
            "approval_spec": {
                "prompt": "ship?",
                "timeout_seconds": 30,
                "default_action": "approve",
            },
        }
        task = Task.from_dict(raw)
        assert task.approval_spec is not None
        assert task.approval_spec.prompt == "ship?"
        assert task.approval_spec.timeout_seconds == 30
        assert task.approval_spec.default_action == "approve"

    def test_from_dict_no_spec_when_absent(self) -> None:
        raw = {
            "id": "T-3",
            "title": "ship",
            "description": "no spec here",
            "role": "backend",
        }
        assert Task.from_dict(raw).approval_spec is None
