"""Unit tests for ManagerAgent upgrade + decomposition logic.

Targets the dark paths in :mod:`bernstein.core.orchestration.manager`:

* :meth:`ManagerAgent._parse_upgrade_changes` - fence stripping + JSON ->
  :class:`FileChange` mapping + error handling.
* :meth:`ManagerAgent._determine_upgrade_type` - keyword-based classification.
* :meth:`ManagerAgent.decompose` / :meth:`ManagerAgent.decompose_sync` -
  subtask-count guards.
* :meth:`ManagerAgent.execute_upgrade` - non-upgrade short-circuit and the
  empty-changes failure branch.

LLM calls are mocked at ``bernstein.core.orchestration.manager.call_llm`` so
no network is required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from bernstein.core.models import (
    Task,
    TaskType,
    UpgradeProposalDetails,
)
from bernstein.core.upgrade_executor import UpgradeType

from bernstein.core.orchestration.manager import ManagerAgent

_LLM = "bernstein.core.orchestration.manager.call_llm"


@pytest.fixture()
def manager(tmp_path: Path) -> ManagerAgent:
    templates = tmp_path / "templates"
    (templates / "roles" / "backend").mkdir(parents=True)
    (templates / "roles" / "backend" / "system_prompt.md").write_text("You are backend.")
    return ManagerAgent(
        server_url="http://127.0.0.1:8052",
        workdir=tmp_path,
        templates_dir=templates,
        model="opus",
    )


# ---------------------------------------------------------------------------
# _parse_upgrade_changes
# ---------------------------------------------------------------------------


def test_parse_upgrade_changes_plain_json(manager: ManagerAgent) -> None:
    raw = '[{"path": "src/x.py", "operation": "modify", "new_content": "print(1)"}]'
    changes = manager._parse_upgrade_changes(raw)
    assert len(changes) == 1
    assert changes[0].path == "src/x.py"
    assert changes[0].operation == "modify"
    assert changes[0].new_content == "print(1)"


def test_parse_upgrade_changes_strips_markdown_fence(manager: ManagerAgent) -> None:
    raw = '```json\n[{"path": "a.py", "operation": "create", "new_content": "x = 1"}]\n```'
    changes = manager._parse_upgrade_changes(raw)
    assert len(changes) == 1
    assert changes[0].path == "a.py"
    assert changes[0].operation == "create"


def test_parse_upgrade_changes_defaults_operation_to_modify(manager: ManagerAgent) -> None:
    raw = '[{"path": "b.py", "new_content": "y = 2"}]'
    changes = manager._parse_upgrade_changes(raw)
    assert changes[0].operation == "modify"


def test_parse_upgrade_changes_invalid_json_returns_empty(manager: ManagerAgent) -> None:
    assert manager._parse_upgrade_changes("not json at all {{{") == []


def test_parse_upgrade_changes_multiple_entries(manager: ManagerAgent) -> None:
    raw = '[{"path": "a.py", "operation": "create", "new_content": "1"},{"path": "b.py", "operation": "delete"}]'
    changes = manager._parse_upgrade_changes(raw)
    assert [c.path for c in changes] == ["a.py", "b.py"]
    assert changes[1].operation == "delete"
    assert changes[1].new_content is None


# ---------------------------------------------------------------------------
# _determine_upgrade_type
# ---------------------------------------------------------------------------


def _upgrade_task(proposed_change: str) -> Task:
    return Task(
        id="up-1",
        title="upgrade",
        description="",
        role="manager",
        task_type=TaskType.UPGRADE_PROPOSAL,
        upgrade_details=UpgradeProposalDetails(proposed_change=proposed_change),
    )


def test_determine_upgrade_type_no_details_defaults_to_code_modification(manager: ManagerAgent) -> None:
    task = Task(id="t", title="x", description="", role="manager")
    assert manager._determine_upgrade_type(task) is UpgradeType.CODE_MODIFICATION


def test_determine_upgrade_type_template(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Update the prompt template")) is UpgradeType.TEMPLATE_UPDATE


def test_determine_upgrade_type_new_agent_role(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Add a new agent role")) is UpgradeType.NEW_AGENT_ROLE


def test_determine_upgrade_type_config(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Tweak a config setting")) is UpgradeType.CONFIG_ADJUSTMENT


def test_determine_upgrade_type_policy(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Change the safety policy rule")) is UpgradeType.POLICY_UPDATE


def test_determine_upgrade_type_routing(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Adjust the routing logic")) is UpgradeType.ROUTING_RULE_CHANGE


def test_determine_upgrade_type_unmatched_defaults_to_code_modification(manager: ManagerAgent) -> None:
    assert manager._determine_upgrade_type(_upgrade_task("Refactor the parser internals")) is (
        UpgradeType.CODE_MODIFICATION
    )


# ---------------------------------------------------------------------------
# decompose / decompose_sync
# ---------------------------------------------------------------------------


@pytest.fixture()
def parent_task() -> Task:
    """Fresh parent task per test.

    Tests run randomly-ordered and must be runnable in isolation; a
    module-level constant could be mutated by ``decompose`` and make the
    suite order-dependent, so each test gets its own instance.
    """
    return Task(id="P-1", title="Big task", description="Do many things", role="backend", estimated_minutes=120)


_THREE_SUBTASKS = (
    "["
    '{"title": "Sub A", "description": "a", "role": "backend", "scope": "small", "complexity": "low", "estimated_minutes": 20},'
    '{"title": "Sub B", "description": "b", "role": "backend", "scope": "small", "complexity": "low", "estimated_minutes": 20},'
    '{"title": "Sub C", "description": "c", "role": "qa", "scope": "small", "complexity": "low", "estimated_minutes": 20}'
    "]"
)


@pytest.mark.asyncio()
async def test_decompose_returns_subtasks_in_range(manager: ManagerAgent, parent_task: Task) -> None:
    with patch(_LLM, new_callable=AsyncMock, return_value=_THREE_SUBTASKS):
        subtasks = await manager.decompose(parent_task, min_subtasks=2, max_subtasks=5)
    assert len(subtasks) == 3
    assert subtasks[0].title == "Sub A"
    # IDs are prefixed with the parent id so children are traceable.
    assert subtasks[0].id.startswith("P-1-subtask")


@pytest.mark.asyncio()
async def test_decompose_raises_when_too_few_subtasks(manager: ManagerAgent, parent_task: Task) -> None:
    one_subtask = (
        '[{"title": "Only one", "description": "x", "role": "backend", '
        '"scope": "small", "complexity": "low", "estimated_minutes": 10}]'
    )
    with patch(_LLM, new_callable=AsyncMock, return_value=one_subtask):
        with pytest.raises(ValueError, match="Expected 2-5 subtasks, got 1"):
            await manager.decompose(parent_task, min_subtasks=2, max_subtasks=5)


@pytest.mark.asyncio()
async def test_decompose_caps_at_max_subtasks(manager: ManagerAgent, parent_task: Task) -> None:
    # Six subtasks returned; with max=5 the count guard (2<=6<=5) is False, so
    # this must raise rather than silently truncate.
    six = (
        "["
        + ",".join(
            f'{{"title": "S{i}", "description": "d", "role": "backend", '
            f'"scope": "small", "complexity": "low", "estimated_minutes": 10}}'
            for i in range(6)
        )
        + "]"
    )
    with patch(_LLM, new_callable=AsyncMock, return_value=six):
        with pytest.raises(ValueError, match="got 6"):
            await manager.decompose(parent_task, min_subtasks=2, max_subtasks=5)


def test_decompose_sync_wraps_async(manager: ManagerAgent, parent_task: Task) -> None:
    with patch(_LLM, new_callable=AsyncMock, return_value=_THREE_SUBTASKS):
        subtasks = manager.decompose_sync(parent_task, min_subtasks=2, max_subtasks=5)
    assert len(subtasks) == 3
    assert {s.role for s in subtasks} == {"backend", "qa"}


# ---------------------------------------------------------------------------
# execute_upgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_execute_upgrade_returns_none_for_non_upgrade_task(manager: ManagerAgent) -> None:
    standard = Task(id="s", title="x", description="", role="backend", task_type=TaskType.STANDARD)
    assert await manager.execute_upgrade(standard) is None


@pytest.mark.asyncio()
async def test_execute_upgrade_returns_none_when_no_upgrade_details(manager: ManagerAgent) -> None:
    # UPGRADE_PROPOSAL type but upgrade_details is None => short-circuit.
    task = Task(id="u", title="x", description="", role="manager", task_type=TaskType.UPGRADE_PROPOSAL)
    assert await manager.execute_upgrade(task) is None


@pytest.mark.asyncio()
async def test_execute_upgrade_returns_none_when_no_changes_generated(manager: ManagerAgent) -> None:
    # Upgrade task with details, but the LLM yields no parseable changes.
    task = _upgrade_task("Modify the core scheduler")
    with patch(_LLM, new_callable=AsyncMock, return_value="garbage not json"):
        # _generate_upgrade_changes returns [] -> ValueError inside -> None.
        assert await manager.execute_upgrade(task) is None


# ---------------------------------------------------------------------------
# _generate_upgrade_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_generate_upgrade_changes_no_details_returns_empty(manager: ManagerAgent) -> None:
    task = Task(id="u", title="x", description="", role="manager", task_type=TaskType.UPGRADE_PROPOSAL)
    assert await manager._generate_upgrade_changes(task) == []


@pytest.mark.asyncio()
async def test_generate_upgrade_changes_parses_llm_output(manager: ManagerAgent) -> None:
    task = _upgrade_task("Modify code")
    payload = '[{"path": "src/y.py", "operation": "modify", "new_content": "z = 3"}]'
    with patch(_LLM, new_callable=AsyncMock, return_value=payload):
        changes = await manager._generate_upgrade_changes(task)
    assert len(changes) == 1
    assert changes[0].path == "src/y.py"


@pytest.mark.asyncio()
async def test_generate_upgrade_changes_swallows_llm_error(manager: ManagerAgent) -> None:
    task = _upgrade_task("Modify code")
    with patch(_LLM, new_callable=AsyncMock, side_effect=RuntimeError("llm down")):
        # The exception is caught and an empty list returned.
        assert await manager._generate_upgrade_changes(task) == []
