"""TEST-018: Test data generators for realistic task payloads.

Factory functions that produce realistic Task, TaskCreate, and
related objects for use in tests throughout the suite.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from bernstein.core.models import (
    CompletionSignal,
    Complexity,
    RiskAssessment,
    RollbackPlan,
    Scope,
    Task,
    TaskStatus,
    TaskType,
    UpgradeProposalDetails,
)
from bernstein.core.quality_gates import QualityGatesConfig

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_task(
    *,
    id: str | None = None,
    title: str = "Test task",
    description: str = "A test task for unit testing",
    role: str = "backend",
    priority: int = 2,
    scope: Scope = Scope.MEDIUM,
    complexity: Complexity = Complexity.MEDIUM,
    status: TaskStatus = TaskStatus.OPEN,
    task_type: TaskType = TaskType.STANDARD,
    depends_on: list[str] | None = None,
    owned_files: list[str] | None = None,
    tenant_id: str = "default",
    assigned_agent: str | None = None,
    parent_task_id: str | None = None,
    completion_signals: list[CompletionSignal] | None = None,
    batch_eligible: bool | None = None,
    created_at: float | None = None,
) -> Task:
    """Create a realistic Task with sensible defaults.

    All parameters are optional; override only what matters for your test.
    """
    return Task(
        id=id or f"task-{uuid.uuid4().hex[:8]}",
        title=title,
        description=description,
        role=role,
        priority=priority,
        scope=scope,
        complexity=complexity,
        status=status,
        task_type=task_type,
        depends_on=depends_on or [],
        owned_files=owned_files or [],
        tenant_id=tenant_id,
        assigned_agent=assigned_agent,
        parent_task_id=parent_task_id,
        completion_signals=completion_signals or [],
        batch_eligible=batch_eligible,
        created_at=created_at or time.time(),
    )


def make_task_create_dict(
    *,
    title: str = "Create-test task",
    description: str = "Created via factory",
    role: str = "backend",
    priority: int = 2,
    scope: str = "medium",
    complexity: str = "medium",
    depends_on: list[str] | None = None,
    owned_files: list[str] | None = None,
    task_type: str = "standard",
    tenant_id: str = "default",
    model: str | None = None,
    effort: str | None = None,
    batch_eligible: bool = False,
) -> dict[str, Any]:
    """Create a dict suitable for TaskCreate.model_validate() or POST /tasks."""
    return {
        "title": title,
        "description": description,
        "role": role,
        "priority": priority,
        "scope": scope,
        "complexity": complexity,
        "depends_on": depends_on or [],
        "owned_files": owned_files or [],
        "task_type": task_type,
        "tenant_id": tenant_id,
        "model": model,
        "effort": effort,
        "batch_eligible": batch_eligible,
    }


def make_upgrade_proposal(
    *,
    title: str = "Upgrade logging",
    risk_level: str = "medium",
    breaking: bool = False,
) -> Task:
    """Create a task with upgrade proposal details."""
    return Task(
        id=f"upgrade-{uuid.uuid4().hex[:8]}",
        title=title,
        description="Upgrade proposal task",
        role="backend",
        task_type=TaskType.UPGRADE_PROPOSAL,
        upgrade_details=UpgradeProposalDetails(
            current_state="Uses print() for logging",
            proposed_change="Switch to structured logging with JSON output",
            benefits=["Better observability", "Easier log parsing"],
            risk_assessment=RiskAssessment(
                level=risk_level,  # type: ignore[arg-type]
                breaking_changes=breaking,
                affected_components=["core.orchestrator", "core.spawner"],
                mitigation="Feature flag for gradual rollout",
            ),
            rollback_plan=RollbackPlan(
                steps=["Revert commit", "Restart servers"],
                estimated_rollback_minutes=15,
            ),
            cost_estimate_usd=0.5,
            performance_impact="Negligible",
        ),
    )


def make_task_batch(
    n: int = 5,
    *,
    role: str = "backend",
    status: TaskStatus = TaskStatus.OPEN,
) -> list[Task]:
    """Create a batch of N tasks with sequential titles."""
    return [
        make_task(
            title=f"Batch task {i + 1}/{n}",
            description=f"Task {i + 1} of {n} in batch",
            role=role,
            status=status,
            priority=(i % 3) + 1,
        )
        for i in range(n)
    ]


def make_completion_signals() -> list[CompletionSignal]:
    """Create a set of typical completion signals."""
    return [
        CompletionSignal(type="path_exists", value="src/new_feature.py"),
        CompletionSignal(type="test_passes", value="pytest tests/unit/test_new_feature.py -x"),
        CompletionSignal(type="file_contains", value="src/new_feature.py::class NewFeature"),
    ]


def make_task_from_dict_raw(
    *,
    status: str = "open",
    role: str = "backend",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a raw dict suitable for Task.from_dict()."""
    base: dict[str, Any] = {
        "id": f"raw-{uuid.uuid4().hex[:8]}",
        "title": "Raw task",
        "description": "Created from raw dict",
        "role": role,
        "priority": 2,
        "scope": "medium",
        "complexity": "medium",
        "status": status,
        "depends_on": [],
        "owned_files": [],
        "assigned_agent": None,
        "result_summary": None,
        "tenant_id": "default",
        "task_type": "standard",
    }
    if extra:
        base.update(extra)
    return base


# Role → typical owned files (realistic per-role file sets)
_ROLE_FILES: dict[str, list[str]] = {
    "backend": [
        "src/bernstein/core/server.py",
        "src/bernstein/core/routes/tasks.py",
        "src/bernstein/core/models.py",
        "tests/unit/test_server.py",
    ],
    "frontend": [
        "src/frontend/components/TaskList.tsx",
        "src/frontend/components/TaskCard.tsx",
        "src/frontend/hooks/useTasks.ts",
        "src/frontend/api/client.ts",
    ],
    "qa": [
        "tests/unit/test_orchestrator.py",
        "tests/integration/test_pipeline.py",
        "tests/e2e/test_full_flow.py",
        "scripts/run_tests.py",
    ],
    "security": [
        "src/bernstein/core/auth_middleware.py",
        "src/bernstein/core/auth_rate_limiter.py",
        "src/bernstein/core/agent_identity.py",
        "tests/unit/test_auth_middleware.py",
    ],
    "devops": [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "Dockerfile",
        "docker-compose.yml",
    ],
}

# Role → required permissions
_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "backend": ["tasks:write", "tasks:read", "bulletin:write"],
    "frontend": ["tasks:read", "status:read"],
    "qa": ["tasks:read", "tasks:write", "status:read"],
    "security": ["tasks:read", "agents:read", "audit:read"],
    "devops": ["tasks:read", "status:read", "agents:read"],
}


def make_multi_file_task(
    *,
    role: str = "backend",
    n_files: int | None = None,
    extra_files: list[str] | None = None,
    **kwargs: Any,
) -> Task:
    """Create a task with multiple realistic owned files for a given role.

    Picks from a predefined per-role file set.  Pass *extra_files* to append
    additional paths on top of the role defaults.

    Args:
        role: Agent role — determines the file set used.
        n_files: How many files to include (defaults to all role files).
        extra_files: Additional file paths appended to the selection.
        **kwargs: Forwarded to :func:`make_task`.

    Returns:
        Task with ``owned_files`` populated with realistic paths.
    """
    base_files = _ROLE_FILES.get(role, _ROLE_FILES["backend"])
    if n_files is not None:
        base_files = base_files[:n_files]
    files = base_files + (extra_files or [])
    return make_task(role=role, owned_files=files, **kwargs)


def make_dependency_chain(
    n: int = 4,
    *,
    role: str = "backend",
) -> list[Task]:
    """Create a linear chain of *n* tasks where each depends on the previous.

    Useful for testing dependency resolution, topological sorting, and
    scheduling logic.

    Args:
        n: Number of tasks in the chain (min 2).
        role: Role assigned to all tasks in the chain.

    Returns:
        List of Tasks in dependency order (index 0 has no deps, index n-1
        depends on all predecessors).
    """
    if n < 2:
        n = 2
    tasks: list[Task] = []
    for i in range(n):
        dep_ids = [tasks[i - 1].id] if i > 0 else []
        tasks.append(
            make_task(
                title=f"Chain step {i + 1}/{n}",
                description=f"Step {i + 1} — depends on previous step",
                role=role,
                priority=1,
                scope=Scope.SMALL,
                depends_on=dep_ids,
            )
        )
    return tasks


def make_quality_gates_config(
    *,
    lint: bool = True,
    tests: bool = True,
    type_check: bool = False,
    pii_scan: bool = True,
    dlp_scan: bool = True,
    mutation_testing: bool = False,
    allow_bypass: bool = False,
) -> QualityGatesConfig:
    """Create a :class:`QualityGatesConfig` with realistic gate settings.

    Suitable for injecting into orchestrator or spawner logic in tests.

    Args:
        lint: Enable lint gate.
        tests: Enable test gate.
        type_check: Enable type-check gate.
        pii_scan: Enable PII scan.
        dlp_scan: Enable DLP scan.
        mutation_testing: Enable mutation testing gate.
        allow_bypass: Whether bypass is permitted.

    Returns:
        A fully-populated :class:`QualityGatesConfig`.
    """
    return QualityGatesConfig(
        enabled=True,
        lint=lint,
        lint_command="ruff check .",
        type_check=type_check,
        type_check_command="pyright",
        tests=tests,
        test_command="uv run python scripts/run_tests.py -x",
        timeout_s=120,
        allow_bypass=allow_bypass,
        cache_enabled=True,
        pii_scan=pii_scan,
        dlp_scan=dlp_scan,
        mutation_testing=mutation_testing,
    )


def make_role_task(
    role: str,
    *,
    with_completion_signals: bool = True,
    with_owned_files: bool = True,
) -> Task:
    """Create a role-appropriate task with realistic files and signals.

    Combines :func:`make_multi_file_task` and :func:`make_completion_signals`
    into a single convenience factory for role-specific test scenarios.

    Args:
        role: Role name (backend, frontend, qa, security, devops).
        with_completion_signals: Include realistic completion signals.
        with_owned_files: Populate ``owned_files`` from the role's file set.

    Returns:
        A fully-populated :class:`Task` tailored to *role*.
    """
    files = _ROLE_FILES.get(role, _ROLE_FILES["backend"]) if with_owned_files else []
    signals = make_completion_signals() if with_completion_signals else []
    return make_task(
        title=f"Role-specific {role} task",
        description=f"Realistic task for the {role} agent role",
        role=role,
        owned_files=files,
        completion_signals=signals,
        priority=2,
    )


# ---------------------------------------------------------------------------
# Tests that the factories themselves work correctly
# ---------------------------------------------------------------------------


class TestMakeTask:
    """Verify make_task produces valid Task objects."""

    def test_default_task(self) -> None:
        t = make_task()
        assert t.id.startswith("task-")
        assert t.status == TaskStatus.OPEN
        assert t.role == "backend"
        assert t.priority == 2

    def test_custom_fields(self) -> None:
        t = make_task(role="qa", priority=1, status=TaskStatus.CLAIMED)
        assert t.role == "qa"
        assert t.priority == 1
        assert t.status == TaskStatus.CLAIMED

    def test_unique_ids(self) -> None:
        ids = {make_task().id for _ in range(20)}
        assert len(ids) == 20

    def test_with_dependencies(self) -> None:
        t = make_task(depends_on=["task-a", "task-b"])
        assert t.depends_on == ["task-a", "task-b"]

    def test_with_completion_signals(self) -> None:
        signals = make_completion_signals()
        t = make_task(completion_signals=signals)
        assert len(t.completion_signals) == 3
        assert t.completion_signals[0].type == "path_exists"


class TestMakeTaskCreateDict:
    """Verify make_task_create_dict produces valid dicts for TaskCreate."""

    def test_validates_with_pydantic(self) -> None:
        from bernstein.core.server import TaskCreate

        data = make_task_create_dict()
        tc = TaskCreate.model_validate(data)
        assert tc.title == "Create-test task"

    def test_custom_fields(self) -> None:
        data = make_task_create_dict(role="security", priority=1, model="opus")
        assert data["role"] == "security"
        assert data["priority"] == 1
        assert data["model"] == "opus"


class TestMakeUpgradeProposal:
    """Verify make_upgrade_proposal produces valid upgrade tasks."""

    def test_has_upgrade_details(self) -> None:
        t = make_upgrade_proposal()
        assert t.task_type == TaskType.UPGRADE_PROPOSAL
        assert t.upgrade_details is not None
        assert t.upgrade_details.risk_assessment.level == "medium"

    def test_breaking_flag(self) -> None:
        t = make_upgrade_proposal(breaking=True)
        assert t.upgrade_details is not None
        assert t.upgrade_details.risk_assessment.breaking_changes is True


class TestMakeTaskBatch:
    """Verify make_task_batch produces correct batches."""

    def test_correct_count(self) -> None:
        batch = make_task_batch(10)
        assert len(batch) == 10

    def test_unique_ids(self) -> None:
        batch = make_task_batch(10)
        ids = {t.id for t in batch}
        assert len(ids) == 10

    def test_priority_distribution(self) -> None:
        batch = make_task_batch(6)
        priorities = [t.priority for t in batch]
        # Priorities cycle: 1, 2, 3, 1, 2, 3
        assert priorities == [1, 2, 3, 1, 2, 3]

    def test_custom_role(self) -> None:
        batch = make_task_batch(3, role="qa")
        assert all(t.role == "qa" for t in batch)

    def test_custom_status(self) -> None:
        batch = make_task_batch(3, status=TaskStatus.CLAIMED)
        assert all(t.status == TaskStatus.CLAIMED for t in batch)


class TestMakeTaskFromDictRaw:
    """Verify make_task_from_dict_raw produces valid raw dicts."""

    def test_task_from_dict(self) -> None:
        raw = make_task_from_dict_raw()
        task = Task.from_dict(raw)
        assert task.status == TaskStatus.OPEN

    def test_extra_fields(self) -> None:
        raw = make_task_from_dict_raw(extra={"model": "opus", "effort": "max"})
        task = Task.from_dict(raw)
        assert task.model == "opus"
        assert task.effort == "max"

    def test_various_statuses(self) -> None:
        for s in ["open", "claimed", "done", "failed"]:
            raw = make_task_from_dict_raw(status=s)
            task = Task.from_dict(raw)
            assert task.status.value == s


class TestMakeMultiFileTask:
    """Verify make_multi_file_task populates owned_files correctly."""

    def test_backend_has_multiple_files(self) -> None:
        t = make_multi_file_task(role="backend")
        assert len(t.owned_files) >= 2
        assert any("server.py" in f or "routes" in f for f in t.owned_files)

    def test_frontend_files(self) -> None:
        t = make_multi_file_task(role="frontend")
        assert any(".tsx" in f or ".ts" in f for f in t.owned_files)

    def test_n_files_limits_count(self) -> None:
        t = make_multi_file_task(role="backend", n_files=2)
        assert len(t.owned_files) == 2

    def test_extra_files_appended(self) -> None:
        extra = ["docs/api.md", "docs/schema.json"]
        t = make_multi_file_task(role="qa", extra_files=extra)
        assert "docs/api.md" in t.owned_files
        assert "docs/schema.json" in t.owned_files

    def test_role_forwarded(self) -> None:
        t = make_multi_file_task(role="security")
        assert t.role == "security"

    def test_unknown_role_falls_back_to_backend(self) -> None:
        t = make_multi_file_task(role="unknown_role")
        assert len(t.owned_files) >= 1


class TestMakeDependencyChain:
    """Verify make_dependency_chain builds correct dependency graphs."""

    def test_chain_length(self) -> None:
        chain = make_dependency_chain(5)
        assert len(chain) == 5

    def test_first_has_no_deps(self) -> None:
        chain = make_dependency_chain(4)
        assert chain[0].depends_on == []

    def test_each_depends_on_previous(self) -> None:
        chain = make_dependency_chain(4)
        for i in range(1, len(chain)):
            assert chain[i - 1].id in chain[i].depends_on

    def test_unique_ids_in_chain(self) -> None:
        chain = make_dependency_chain(6)
        ids = {t.id for t in chain}
        assert len(ids) == 6

    def test_minimum_chain_length(self) -> None:
        chain = make_dependency_chain(1)  # clamped to 2
        assert len(chain) == 2

    def test_role_applied(self) -> None:
        chain = make_dependency_chain(3, role="qa")
        assert all(t.role == "qa" for t in chain)


class TestMakeQualityGatesConfig:
    """Verify make_quality_gates_config returns a valid QualityGatesConfig."""

    def test_default_config(self) -> None:
        cfg = make_quality_gates_config()
        assert cfg.enabled is True
        assert cfg.lint is True
        assert cfg.tests is True

    def test_custom_gates(self) -> None:
        cfg = make_quality_gates_config(lint=False, tests=False, type_check=True)
        assert cfg.lint is False
        assert cfg.tests is False
        assert cfg.type_check is True

    def test_mutation_disabled_by_default(self) -> None:
        cfg = make_quality_gates_config()
        assert cfg.mutation_testing is False

    def test_mutation_enabled(self) -> None:
        cfg = make_quality_gates_config(mutation_testing=True)
        assert cfg.mutation_testing is True

    def test_bypass_off_by_default(self) -> None:
        cfg = make_quality_gates_config()
        assert cfg.allow_bypass is False

    def test_pii_and_dlp_defaults(self) -> None:
        cfg = make_quality_gates_config()
        assert cfg.pii_scan is True
        assert cfg.dlp_scan is True


class TestMakeRoleTask:
    """Verify make_role_task produces role-appropriate tasks."""

    def test_backend_role(self) -> None:
        t = make_role_task("backend")
        assert t.role == "backend"
        assert len(t.owned_files) >= 2
        assert len(t.completion_signals) == 3

    def test_qa_role(self) -> None:
        t = make_role_task("qa")
        assert t.role == "qa"
        assert any("test" in f.lower() for f in t.owned_files)

    def test_without_signals(self) -> None:
        t = make_role_task("backend", with_completion_signals=False)
        assert t.completion_signals == []

    def test_without_owned_files(self) -> None:
        t = make_role_task("backend", with_owned_files=False)
        assert t.owned_files == []

    def test_all_known_roles(self) -> None:
        for role in ["backend", "frontend", "qa", "security", "devops"]:
            t = make_role_task(role)
            assert t.role == role
            assert len(t.owned_files) >= 1
