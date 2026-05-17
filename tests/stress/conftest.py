"""Shared fixtures and helpers for the ``tests/stress/`` suite.

The stress tests are gated behind ``@pytest.mark.stress`` (declared in
``pyproject.toml``); the autouse fixture here turns "ran without the
marker selected" into a clean skip so contributors who run
``pytest tests/stress`` directly see a deliberate skip message rather
than a wall of probes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def make_task_request(
    *,
    title: str = "stress task",
    description: str = "stress test task",
    role: str = "backend",
    priority: int = 1,
    scope: str = "small",
    complexity: str = "low",
) -> Any:
    """Build a minimal ``TaskCreateRequest`` compatible namespace.

    Mirrors the helper in ``tests/unit/test_task_store.py`` but kept
    local to avoid cross-suite coupling.  Stress tests create thousands
    of these per run, so all expensive optional fields stay ``None``.
    """

    return SimpleNamespace(
        title=title,
        description=description,
        role=role,
        priority=priority,
        scope=scope,
        complexity=complexity,
        estimated_minutes=5,
        depends_on=[],
        owned_files=[],
        cell_id=None,
        task_type="standard",
        upgrade_details=None,
        model=None,
        effort=None,
        batch_eligible=False,
        completion_signals=[],
        slack_context=None,
        tenant_id="default",
        repo=None,
        depends_on_repo=None,
        parent_task_id=None,
        parent_session_id=None,
        parent_context=None,
        retry_count=None,
        max_retries=None,
        retry_delay_s=None,
        terminal_reason=None,
        max_output_tokens=None,
        meta_messages=None,
        cli=None,
        approval_required=False,
        eu_ai_act_risk="minimal",
        risk_level="low",
        metadata={},
    )
