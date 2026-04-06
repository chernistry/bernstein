"""CFG-013: Config lint with best practice suggestions.

Analyzes a bernstein.yaml config and produces warnings, suggestions,
and best-practice recommendations.  Unlike validation (which checks
correctness), linting checks for suboptimal or risky patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LintFinding:
    """A single lint finding for a config.

    Attributes:
        rule: Rule identifier (e.g. "no-budget-set").
        severity: "info", "warning", or "error".
        message: Human-readable description.
        key: Config key path that triggered the finding.
        suggestion: Recommended fix or improvement.
    """

    rule: str
    severity: Literal["info", "warning", "error"]
    message: str
    key: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        """Serialize to a dict."""
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "key": self.key,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class LintReport:
    """Collection of lint findings for a config.

    Attributes:
        findings: All findings from the lint pass.
        error_count: Number of error-severity findings.
        warning_count: Number of warning-severity findings.
        info_count: Number of info-severity findings.
    """

    findings: list[LintFinding] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    @property
    def has_errors(self) -> bool:
        """Whether any error-severity findings exist."""
        return self.error_count > 0

    @property
    def has_warnings(self) -> bool:
        """Whether any warning-severity findings exist."""
        return self.warning_count > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report."""
        return {
            "findings": [f.to_dict() for f in self.findings],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
        }


# ---------------------------------------------------------------------------
# Lint rules
# ---------------------------------------------------------------------------


def _check_no_budget(config: dict[str, Any]) -> list[LintFinding]:
    """Warn when no budget is set."""
    budget = config.get("budget")
    if budget is None:
        return [
            LintFinding(
                rule="no-budget-set",
                severity="warning",
                message="No budget limit is configured. Agents may spend without a cap.",
                key="budget",
                suggestion="Set budget: '$20' or budget: 20 to limit spending.",
            )
        ]
    return []


def _check_high_max_agents(config: dict[str, Any]) -> list[LintFinding]:
    """Warn when max_agents is unusually high."""
    max_agents = config.get("max_agents", 6)
    if isinstance(max_agents, int) and max_agents > 12:
        return [
            LintFinding(
                rule="high-max-agents",
                severity="warning",
                message=f"max_agents is {max_agents}, which may cause rate limiting or high costs.",
                key="max_agents",
                suggestion="Consider max_agents: 6-8 for most projects.",
            )
        ]
    return []


def _check_auto_merge_without_gates(config: dict[str, Any]) -> list[LintFinding]:
    """Error when auto_merge is on but quality gates are off."""
    auto_merge = config.get("auto_merge", True)
    gates = config.get("quality_gates", {})
    gates_enabled = gates.get("enabled", True) if isinstance(gates, dict) else True

    if auto_merge and not gates_enabled:
        return [
            LintFinding(
                rule="auto-merge-no-gates",
                severity="error",
                message="auto_merge is enabled but quality_gates are disabled. "
                "This allows unvalidated code to be merged automatically.",
                key="auto_merge",
                suggestion="Enable quality_gates or disable auto_merge.",
            )
        ]
    return []


def _check_no_tests_in_gates(config: dict[str, Any]) -> list[LintFinding]:
    """Warn when quality gates are on but tests are off."""
    gates = config.get("quality_gates", {})
    if not isinstance(gates, dict):
        return []
    if gates.get("enabled", True) and not gates.get("tests", False):
        return [
            LintFinding(
                rule="gates-no-tests",
                severity="info",
                message="Quality gates are enabled but test execution is off.",
                key="quality_gates.tests",
                suggestion="Enable tests: true in quality_gates for CI safety.",
            )
        ]
    return []


def _check_direct_merge_production(config: dict[str, Any]) -> list[LintFinding]:
    """Warn when merge_strategy is 'direct' (bypasses code review)."""
    if config.get("merge_strategy") == "direct":
        return [
            LintFinding(
                rule="direct-merge-risky",
                severity="warning",
                message="merge_strategy: direct bypasses pull request review.",
                key="merge_strategy",
                suggestion="Use merge_strategy: pr for code review on all changes.",
            )
        ]
    return []


def _check_evolution_without_llm(config: dict[str, Any]) -> list[LintFinding]:
    """Error when evolution is on but LLM provider is missing."""
    if config.get("evolution_enabled", True):
        provider = config.get("internal_llm_provider", "")
        if provider in ("none", ""):
            return [
                LintFinding(
                    rule="evolution-no-llm",
                    severity="error",
                    message="evolution_enabled requires an LLM provider but internal_llm_provider is not configured.",
                    key="evolution_enabled",
                    suggestion="Set internal_llm_provider or disable evolution_enabled.",
                )
            ]
    return []


def _check_no_goal(config: dict[str, Any]) -> list[LintFinding]:
    """Error when the goal field is missing or empty."""
    goal = config.get("goal", "")
    if not goal or (isinstance(goal, str) and not goal.strip()):
        return [
            LintFinding(
                rule="missing-goal",
                severity="error",
                message="The 'goal' field is required and must not be empty.",
                key="goal",
                suggestion="Set goal: 'Your project objective here'.",
            )
        ]
    return []


def _check_single_agent_team(config: dict[str, Any]) -> list[LintFinding]:
    """Info when team has only one role (may not need orchestration)."""
    team = config.get("team", "auto")
    if isinstance(team, list) and len(team) == 1:
        return [
            LintFinding(
                rule="single-role-team",
                severity="info",
                message=f"Team has only one role: {team[0]}. Multi-agent orchestration may not be needed.",
                key="team",
                suggestion="Use team: auto or add more roles for parallel work.",
            )
        ]
    return []


# All lint rule functions.
_LINT_RULES = (
    _check_no_budget,
    _check_high_max_agents,
    _check_auto_merge_without_gates,
    _check_no_tests_in_gates,
    _check_direct_merge_production,
    _check_evolution_without_llm,
    _check_no_goal,
    _check_single_agent_team,
)


def lint_config(config: dict[str, Any]) -> LintReport:
    """Run all lint rules against a config dict.

    Args:
        config: Parsed bernstein.yaml config dict.

    Returns:
        LintReport with all findings.
    """
    findings: list[LintFinding] = []

    for rule_fn in _LINT_RULES:
        findings.extend(rule_fn(config))

    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    info_count = sum(1 for f in findings if f.severity == "info")

    return LintReport(
        findings=findings,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
    )
