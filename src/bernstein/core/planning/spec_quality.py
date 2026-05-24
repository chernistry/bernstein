"""Spec-quality checklist gate (issue #1631).

When an operator authors a feature spec, this module evaluates it against a
small set of deterministic content rules and refuses to advance into task
generation until every required rule passes.

Rules return a :class:`RuleResult` with a pass/fail flag and an optional
``hint`` describing how to fix the violation. :func:`evaluate` aggregates
results into a :class:`ChecklistReport`. Pipeline integration drives the
auto-fix loop via :func:`refuse_to_advance`.

Rules are pluggable via the ``bernstein.spec_quality_rules`` entry-points
group: each entry resolves to a zero-arg callable that returns a
:class:`Rule`. Default rules check for the presence of:

* an *Acceptance criteria* heading,
* an *Out of scope* heading,
* no ``TODO`` markers in spec body,
* no placeholder strings (``<...>``, ``TBD``, ``XXX``),
* every referenced ``path/file`` exists on disk relative to the workspace
  root (best-effort; missing workspace = skip the rule with a hint).

The module is deliberately library-only: the CLI surface lives in
:mod:`bernstein.cli.commands.spec_cmd` and pipeline integration is driven
by the orchestrator. The functions here have no I/O side effects beyond
reading the spec path passed in by the caller.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_AUTO_FIX_ITERATIONS",
    "ChecklistReport",
    "Rule",
    "RuleResult",
    "SpecQualityUnresolvedError",
    "auto_fix_loop",
    "default_rules",
    "evaluate",
    "load_plugin_rules",
    "refuse_to_advance",
    "render_report",
]

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

#: Default upper bound on auto-fix iterations before the gate refuses to
#: advance. Aligns with the issue spec ("up to 3 iterations").
DEFAULT_MAX_AUTO_FIX_ITERATIONS: int = 3

#: Entry-point group name used to register third-party rules.
ENTRY_POINT_GROUP: str = "bernstein.spec_quality_rules"


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Outcome of a single :class:`Rule` evaluation against a spec.

    Attributes:
        rule_id: Stable identifier of the producing rule (matches
            :attr:`Rule.rule_id`).
        passed: ``True`` when the spec satisfies the rule.
        message: Human-readable summary; empty string when ``passed``.
        hint: Optional remediation hint for the operator / auto-fix loop.
    """

    rule_id: str
    passed: bool
    message: str = ""
    hint: str = ""


class _RuleCheck(Protocol):
    """Callable signature for rule evaluation.

    Implementations must be pure functions of (spec_text, workspace_root).
    They must not raise; any failure to evaluate is reported as a
    :class:`RuleResult` with ``passed=False``.
    """

    def __call__(self, spec_text: str, workspace_root: Path | None) -> RuleResult: ...


@dataclass(frozen=True, slots=True)
class Rule:
    """A pluggable spec-quality rule.

    Attributes:
        rule_id: Stable, slug-style identifier (``[a-z0-9_-]+``).
        description: One-line human description of what the rule checks.
        check: Callable that returns a :class:`RuleResult`.
        required: When ``True`` (default), a failed result blocks advancement.
            Optional rules are reported but never gate the pipeline.
    """

    rule_id: str
    description: str
    check: _RuleCheck
    required: bool = True

    def evaluate(self, spec_text: str, workspace_root: Path | None) -> RuleResult:
        """Run the rule against ``spec_text``, swallowing any exceptions.

        The ``rule_id`` of the returned result is normalised to this rule's
        own ``rule_id`` so a misbehaving plugin cannot smuggle a mismatched
        id past the ``required_failures`` filter.
        """
        try:
            result = self.check(spec_text, workspace_root)
            if result.rule_id != self.rule_id:
                return RuleResult(
                    rule_id=self.rule_id,
                    passed=result.passed,
                    message=result.message or f"rule returned mismatched id '{result.rule_id}'",
                    hint=result.hint,
                )
            return result
        except Exception as exc:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message=f"rule raised {type(exc).__name__}: {exc}",
                hint="Fix the rule implementation or remove it from the registry.",
            )


@dataclass(frozen=True, slots=True)
class ChecklistReport:
    """Aggregated outcome of evaluating every rule against a spec.

    Attributes:
        spec_path: Source path of the evaluated spec (informational).
        results: Ordered list of :class:`RuleResult` records, one per rule.
        iteration: Loop counter from :func:`auto_fix_loop` (0 = single eval).
        required_rule_ids: Set of rule ids whose failure blocks advancement.
            Populated by :func:`evaluate`; an empty set means "treat every
            failed result as required" (back-compat for hand-built reports).
    """

    spec_path: Path
    results: tuple[RuleResult, ...]
    iteration: int = 0
    required_rule_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def passed(self) -> bool:
        """``True`` when every *required* rule passed."""
        return not self.required_failures

    @property
    def required_failures(self) -> tuple[RuleResult, ...]:
        """Failed results that block advancement."""
        required = self.required_rule_ids
        if not required:
            return tuple(r for r in self.results if not r.passed)
        return tuple(r for r in self.results if not r.passed and r.rule_id in required)

    @property
    def failure_count(self) -> int:
        """Number of failed results regardless of ``required`` flag."""
        return sum(1 for r in self.results if not r.passed)


# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------

_RE_HEADING_ACCEPTANCE = re.compile(r"^\s{0,3}#{1,6}\s*acceptance\s+criteria\b", re.IGNORECASE | re.MULTILINE)
_RE_HEADING_OUT_OF_SCOPE = re.compile(r"^\s{0,3}#{1,6}\s*out[\s-]+of[\s-]+scope\b", re.IGNORECASE | re.MULTILINE)
_RE_HEADING_TESTED_VIA = re.compile(r"^\s{0,3}#{1,6}\s*tested[\s-]+via\b", re.IGNORECASE | re.MULTILINE)
_RE_TODO = re.compile(r"\bTODO\b", re.IGNORECASE)
_RE_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_\s-]{2,}>|\bTBD\b|\bXXX\b")
_RE_PATH_TOKEN = re.compile(r"`(?P<path>[\w./-]+\.[A-Za-z0-9]{1,6})`")


def _check_acceptance_criteria(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Require a ``## Acceptance criteria`` heading."""
    del workspace_root
    if _RE_HEADING_ACCEPTANCE.search(spec_text):
        return RuleResult(rule_id="acceptance_criteria_present", passed=True)
    return RuleResult(
        rule_id="acceptance_criteria_present",
        passed=False,
        message="Spec is missing an 'Acceptance criteria' heading.",
        hint="Add a section '## Acceptance criteria' with one or more bullet items.",
    )


def _check_out_of_scope(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Require a ``## Out of scope`` heading."""
    del workspace_root
    if _RE_HEADING_OUT_OF_SCOPE.search(spec_text):
        return RuleResult(rule_id="out_of_scope_present", passed=True)
    return RuleResult(
        rule_id="out_of_scope_present",
        passed=False,
        message="Spec is missing an 'Out of scope' heading.",
        hint="Add a section '## Out of scope' listing what this spec explicitly excludes.",
    )


def _check_tested_via(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Require either a ``Tested via`` section or the substring in body."""
    del workspace_root
    if _RE_HEADING_TESTED_VIA.search(spec_text):
        return RuleResult(rule_id="tested_via_present", passed=True)
    if re.search(r"\btested\s+via\b", spec_text, re.IGNORECASE):
        return RuleResult(rule_id="tested_via_present", passed=True)
    return RuleResult(
        rule_id="tested_via_present",
        passed=False,
        message="Spec does not mention how the change will be tested.",
        hint=(
            "Add a 'Tested via' heading or sentence describing the test files / pytest selectors that cover the change."
        ),
    )


def _check_no_todo(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Forbid ``TODO`` markers in the spec body."""
    del workspace_root
    matches = _RE_TODO.findall(spec_text)
    if not matches:
        return RuleResult(rule_id="no_todo_markers", passed=True)
    return RuleResult(
        rule_id="no_todo_markers",
        passed=False,
        message=f"Spec contains {len(matches)} TODO marker(s).",
        hint="Resolve every TODO into a concrete acceptance bullet before advancing.",
    )


def _check_no_placeholders(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Forbid ``<PLACEHOLDER>``, ``TBD``, ``XXX`` tokens."""
    del workspace_root
    matches = _RE_PLACEHOLDER.findall(spec_text)
    if not matches:
        return RuleResult(rule_id="no_placeholder_tokens", passed=True)
    sample = ", ".join(sorted(set(matches))[:3])
    return RuleResult(
        rule_id="no_placeholder_tokens",
        passed=False,
        message=f"Spec contains placeholder tokens (e.g. {sample}).",
        hint="Replace placeholder tokens with concrete values.",
    )


def _extract_path_tokens(spec_text: str) -> list[str]:
    """Return every backtick-quoted path-like token from the spec."""
    return [m.group("path") for m in _RE_PATH_TOKEN.finditer(spec_text)]


def _check_ref_paths_exist(spec_text: str, workspace_root: Path | None) -> RuleResult:
    """Every backtick-quoted path token must resolve under ``workspace_root``.

    When ``workspace_root`` is ``None`` the rule passes with an informational
    hint - we cannot verify paths without a checkout.
    """
    if workspace_root is None:
        return RuleResult(
            rule_id="ref_paths_exist",
            passed=True,
            hint="Workspace root not supplied; skipping path-existence check.",
        )
    tokens = _extract_path_tokens(spec_text)
    if not tokens:
        return RuleResult(rule_id="ref_paths_exist", passed=True)
    missing: list[str] = []
    for token in tokens:
        # Heuristic: only treat tokens that look like actual repo paths
        # (contain a directory separator) as referenced. Bare filenames
        # like ``README.md`` are ambiguous and are skipped to keep the
        # rule low-false-positive.
        if "/" not in token:
            continue
        candidate = (workspace_root / token).resolve()
        try:
            candidate.relative_to(workspace_root.resolve())
        except ValueError:
            # Escapes workspace -- treat as missing.
            missing.append(token)
            continue
        if not candidate.exists():
            missing.append(token)
    if not missing:
        return RuleResult(rule_id="ref_paths_exist", passed=True)
    sample = ", ".join(missing[:3])
    return RuleResult(
        rule_id="ref_paths_exist",
        passed=False,
        message=f"{len(missing)} referenced path(s) do not exist (e.g. {sample}).",
        hint="Fix typos or create the referenced files before advancing.",
    )


def default_rules() -> list[Rule]:
    """Return the built-in rule set in deterministic evaluation order."""
    return [
        Rule(
            rule_id="acceptance_criteria_present",
            description="Spec has an 'Acceptance criteria' section.",
            check=_check_acceptance_criteria,
        ),
        Rule(
            rule_id="out_of_scope_present",
            description="Spec has an 'Out of scope' section.",
            check=_check_out_of_scope,
        ),
        Rule(
            rule_id="tested_via_present",
            description="Spec describes how the change will be tested.",
            check=_check_tested_via,
        ),
        Rule(
            rule_id="no_todo_markers",
            description="Spec contains no TODO markers.",
            check=_check_no_todo,
        ),
        Rule(
            rule_id="no_placeholder_tokens",
            description="Spec contains no <PLACEHOLDER>/TBD/XXX tokens.",
            check=_check_no_placeholders,
        ),
        Rule(
            rule_id="ref_paths_exist",
            description="Every referenced path exists in the workspace.",
            check=_check_ref_paths_exist,
        ),
    ]


# ---------------------------------------------------------------------------
# Entry-point loading
# ---------------------------------------------------------------------------


def load_plugin_rules() -> list[Rule]:
    """Load rules registered via the ``bernstein.spec_quality_rules`` group.

    Each entry-point must resolve to a zero-arg callable returning a
    :class:`Rule`. Errors during discovery or instantiation are swallowed
    and the offending plugin is omitted; we never let a third-party rule
    break the gate.
    """
    rules: list[Rule] = []
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        logger.warning(
            "Failed to discover spec-quality entry points for group %s",
            ENTRY_POINT_GROUP,
            exc_info=True,
        )
        return rules
    for ep in eps:
        try:
            factory = ep.load()
            rule = factory()
        except Exception:
            logger.warning(
                "Failed to load spec-quality plugin rule: %s",
                ep.name,
                exc_info=True,
            )
            continue
        if isinstance(rule, Rule):
            rules.append(rule)
    return rules


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    spec: Path | str,
    *,
    workspace_root: Path | None = None,
    rules: Sequence[Rule] | None = None,
) -> ChecklistReport:
    """Evaluate every rule against ``spec`` and return a :class:`ChecklistReport`.

    Args:
        spec: Path to a markdown spec file *or* the raw spec text.
        workspace_root: Optional repo root used by path-existence rules.
        rules: Override the default rule set (typically used in tests).
    """
    spec_path, spec_text = _resolve_spec_input(spec)
    effective_rules = list(rules) if rules is not None else (default_rules() + load_plugin_rules())
    # Deduplicate by rule_id (keep first occurrence) so a plugin cannot
    # reuse a default rule's id and flip its required/optional status.
    seen: set[str] = set()
    deduped_rules: list[Rule] = []
    for rule in effective_rules:
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        deduped_rules.append(rule)
    effective_rules = deduped_rules
    results = tuple(r.evaluate(spec_text, workspace_root) for r in effective_rules)
    required_ids = frozenset(r.rule_id for r in effective_rules if r.required)
    return ChecklistReport(
        spec_path=spec_path,
        results=results,
        required_rule_ids=required_ids,
    )


def _resolve_spec_input(spec: Path | str) -> tuple[Path, str]:
    """Normalise the ``spec`` argument into ``(path, text)``.

    A :class:`Path` is read from disk; a ``str`` is treated as raw spec
    content unless it points at an existing file, in which case the file
    contents are returned. The path component of the return value is
    informational only - callers persist it on the report for display.
    """
    if isinstance(spec, Path):
        return spec, spec.read_text(encoding="utf-8")
    # Inline spec text that is not a valid filesystem path (e.g. embedded
    # NUL bytes or an over-long name) falls back to inline mode.
    with contextlib.suppress(OSError):
        candidate = Path(spec)
        if candidate.exists() and candidate.is_file():
            return candidate, candidate.read_text(encoding="utf-8")
    return Path("<inline>"), spec


# ---------------------------------------------------------------------------
# Auto-fix loop + refuse-to-advance
# ---------------------------------------------------------------------------


class SpecQualityUnresolvedError(RuntimeError):
    """Raised by :func:`refuse_to_advance` when the gate refuses the pipeline.

    Attributes:
        report: The final :class:`ChecklistReport` after the last attempt.
    """

    def __init__(self, report: ChecklistReport) -> None:
        super().__init__(
            f"Spec-quality gate refused to advance after {report.iteration} iteration(s); "
            f"{len(report.required_failures)} required rule(s) still failing."
        )
        self.report = report


def auto_fix_loop(
    spec: Path | str,
    *,
    workspace_root: Path | None = None,
    rules: Sequence[Rule] | None = None,
    autofix: Callable[[ChecklistReport], str | None] | None = None,
    max_iterations: int = DEFAULT_MAX_AUTO_FIX_ITERATIONS,
) -> ChecklistReport:
    """Iterate evaluate -> autofix until the report passes or budget is spent.

    The ``autofix`` callable receives the latest report and must return the
    rewritten spec text (or ``None`` to abort the loop without further
    attempts). When ``autofix`` is ``None`` the loop degenerates to a
    single evaluate call. The returned report's ``iteration`` field
    reflects how many *fix attempts* were spent (0 = passed on first
    evaluate, ``max_iterations`` = budget exhausted).
    """
    if max_iterations < 0:
        raise ValueError("max_iterations must be >= 0")
    current_text: Path | str = spec
    report = evaluate(current_text, workspace_root=workspace_root, rules=rules)
    if report.passed or autofix is None or max_iterations == 0:
        return report

    original_path = report.spec_path
    attempts = 0
    while attempts < max_iterations and not report.passed:
        attempts += 1
        rewritten = autofix(report)
        if rewritten is None:
            break
        current_text = rewritten
        report = evaluate(current_text, workspace_root=workspace_root, rules=rules)
        report = ChecklistReport(
            spec_path=original_path,
            results=report.results,
            iteration=attempts,
            required_rule_ids=report.required_rule_ids,
        )
    return report


def refuse_to_advance(
    spec: Path | str,
    *,
    workspace_root: Path | None = None,
    rules: Sequence[Rule] | None = None,
    autofix: Callable[[ChecklistReport], str | None] | None = None,
    max_iterations: int = DEFAULT_MAX_AUTO_FIX_ITERATIONS,
) -> ChecklistReport:
    """Drive the auto-fix loop and raise if the gate is still failing.

    Returns the final :class:`ChecklistReport` when the gate passes; raises
    :class:`SpecQualityUnresolvedError` otherwise. Use this in pipeline
    integration just before dispatching the implementer agent.
    """
    report = auto_fix_loop(
        spec,
        workspace_root=workspace_root,
        rules=rules,
        autofix=autofix,
        max_iterations=max_iterations,
    )
    if not report.passed:
        raise SpecQualityUnresolvedError(report)
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_report(report: ChecklistReport) -> str:
    """Render ``report`` as a human-readable markdown block.

    The output is stable and safe to write to a checklist artefact alongside
    the spec or to stream from the CLI.
    """
    lines: list[str] = [
        f"# Spec-quality checklist for `{report.spec_path}`",
        "",
        f"Iteration: {report.iteration} | Passed: {len(report.results) - report.failure_count}/{len(report.results)}",
        "",
    ]
    for result in report.results:
        marker = "[x]" if result.passed else "[ ]"
        lines.append(f"- {marker} `{result.rule_id}`")
        if not result.passed:
            if result.message:
                lines.append(f"  - {result.message}")
            if result.hint:
                lines.append(f"  - hint: {result.hint}")
    return "\n".join(lines) + "\n"
