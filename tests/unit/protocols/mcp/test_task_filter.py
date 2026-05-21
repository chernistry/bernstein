"""Tests for per-task MCP server filtering (MCP-010).

Covers ``mcp_task_filter``:

* ``RoleServerRule.matches_role`` (wildcard + exact) and
  ``matches_scope`` (no-pattern allow, regex hit / miss).
* ``TaskMCPFilter.filter_for_task`` decision tree: explicit
  ``task.mcp_servers`` list, rule-derived allow set, scope gating, and
  the ``default_allow_all`` vs deny-by-default fallbacks.
* ``filter_names`` convenience + ``to_dict`` config serialisation +
  ``FilterResult.to_dict``.

Pure role/scope arithmetic - deterministic.
"""

from __future__ import annotations

from bernstein.core.models import Complexity, Scope, Task, TaskType

from bernstein.core.protocols.mcp.mcp_task_filter import (
    FilterResult,
    RoleServerRule,
    TaskMCPFilter,
)


def _task(
    role: str = "backend",
    *,
    owned_files: list[str] | None = None,
    mcp_servers: list[str] | None = None,
) -> Task:
    return Task(
        id="t1",
        title="Do something",
        description="desc",
        role=role,
        complexity=Complexity.MEDIUM,
        scope=Scope.MEDIUM,
        priority=2,
        owned_files=owned_files or [],
        estimated_minutes=30,
        task_type=TaskType.STANDARD,
        metadata={},
        mcp_servers=mcp_servers or [],
    )


class TestRoleServerRule:
    def test_matches_exact_role(self) -> None:
        rule = RoleServerRule(role="backend")
        assert rule.matches_role("backend") is True
        assert rule.matches_role("qa") is False

    def test_wildcard_matches_any_role(self) -> None:
        rule = RoleServerRule(role="*")
        assert rule.matches_role("anything") is True

    def test_no_scope_patterns_always_matches(self) -> None:
        rule = RoleServerRule(role="backend")
        assert rule.matches_scope([]) is True
        assert rule.matches_scope(["whatever.py"]) is True

    def test_scope_pattern_hit(self) -> None:
        rule = RoleServerRule(role="backend", scope_patterns=(r"\.py$",))
        assert rule.matches_scope(["src/api.py"]) is True

    def test_scope_pattern_miss(self) -> None:
        rule = RoleServerRule(role="backend", scope_patterns=(r"\.py$",))
        assert rule.matches_scope(["README.md"]) is False

    def test_scope_pattern_case_insensitive(self) -> None:
        rule = RoleServerRule(role="backend", scope_patterns=(r"readme",))
        assert rule.matches_scope(["README.MD"]) is True


class TestFilterExplicit:
    def test_explicit_mcp_servers_used(self) -> None:
        f = TaskMCPFilter()
        task = _task(mcp_servers=["gh", "fs"])
        result = f.filter_for_task(task, ["gh", "fs", "slack"])
        assert set(result.allowed) == {"gh", "fs"}
        assert result.blocked == ["slack"]
        assert "slack" in result.reasons

    def test_explicit_overrides_rules(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["everything"])
        task = _task(role="backend", mcp_servers=["only-this"])
        result = f.filter_for_task(task, ["only-this", "everything"])
        # explicit list wins over the role rule.
        assert result.allowed == ["only-this"]


class TestFilterByRule:
    def test_rule_allows_named_servers(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["gh", "fs"])
        result = f.filter_for_task(_task(role="backend"), ["gh", "fs", "slack"])
        assert set(result.allowed) == {"gh", "fs"}
        assert result.blocked == ["slack"]

    def test_wildcard_rule_applies_to_all_roles(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("*", ["common"])
        result = f.filter_for_task(_task(role="security"), ["common", "other"])
        assert result.allowed == ["common"]

    def test_scope_pattern_gates_rule(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["db"], scope_patterns=[r"migrations/"])
        # owns a migration file -> rule applies.
        hit = f.filter_for_task(_task(role="backend", owned_files=["migrations/001.sql"]), ["db"])
        assert hit.allowed == ["db"]
        # owns no matching file -> rule does not apply -> deny-by-default.
        miss = f.filter_for_task(_task(role="backend", owned_files=["src/api.py"]), ["db"])
        assert miss.allowed == []
        assert miss.blocked == ["db"]

    def test_multiple_rules_union(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["a"])
        f.add_role_rule("*", ["b"])
        result = f.filter_for_task(_task(role="backend"), ["a", "b", "c"])
        assert set(result.allowed) == {"a", "b"}


class TestDefaultPolicies:
    def test_no_rules_allows_all(self) -> None:
        # With no rules at all, every server passes through.
        f = TaskMCPFilter()
        result = f.filter_for_task(_task(role="backend"), ["a", "b"])
        assert set(result.allowed) == {"a", "b"}

    def test_default_allow_all_when_no_match(self) -> None:
        f = TaskMCPFilter(default_allow_all=True)
        f.add_role_rule("qa", ["x"])  # rule exists, but task role is backend
        result = f.filter_for_task(_task(role="backend"), ["a", "b"])
        assert set(result.allowed) == {"a", "b"}

    def test_deny_by_default_when_no_match(self) -> None:
        f = TaskMCPFilter(default_allow_all=False)
        f.add_role_rule("qa", ["x"])  # no rule for backend
        result = f.filter_for_task(_task(role="backend"), ["a", "b"])
        assert result.allowed == []
        assert set(result.blocked) == {"a", "b"}
        assert all("no matching rule" in r for r in result.reasons.values())


class TestConvenienceAndSerialisation:
    def test_filter_names_returns_allowed_only(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["gh"])
        names = f.filter_names(_task(role="backend"), ["gh", "slack"])
        assert names == ["gh"]

    def test_rules_property_returns_copy(self) -> None:
        f = TaskMCPFilter()
        f.add_role_rule("backend", ["gh"])
        rules = f.rules
        rules.clear()
        # mutating the returned list must not clear the internal rules.
        assert len(f.rules) == 1

    def test_to_dict_serialises_config(self) -> None:
        f = TaskMCPFilter(default_allow_all=True)
        f.add_role_rule("backend", ["gh"], scope_patterns=[r"\.py$"])
        d = f.to_dict()
        assert d["default_allow_all"] is True
        assert d["rules"][0]["role"] == "backend"
        assert d["rules"][0]["allowed_servers"] == ["gh"]
        assert d["rules"][0]["scope_patterns"] == [r"\.py$"]

    def test_filter_result_to_dict(self) -> None:
        result = FilterResult(task_id="t1", role="backend", allowed=["a"], blocked=["b"], reasons={"b": "nope"})
        d = result.to_dict()
        assert d["task_id"] == "t1"
        assert d["allowed"] == ["a"]
        assert d["reasons"] == {"b": "nope"}
