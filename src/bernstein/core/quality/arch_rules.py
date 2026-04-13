"""Architecture conformance checking against declared module boundaries (#682).

Provides AST-based static analysis of import statements across a project,
validating them against declarative boundary rules loaded from YAML config.
Unlike the diff-based ``arch_conformance`` module (which checks only new
imports in a git diff), this module scans actual source files on disk and
reports all violations — suitable for CI, pre-commit hooks, and full-project
audits.

Example YAML config::

    boundaries:
      - module: bernstein.core
        allowed_imports:
          - bernstein.core
          - bernstein.adapters
        denied_imports: []
      - module: bernstein.cli
        allowed_imports: []
        denied_imports:
          - bernstein.adapters
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryRule:
    """Declares import boundaries for a logical module.

    Attributes:
        module: Dotted module prefix this rule applies to
            (e.g. ``"bernstein.core"``).
        allowed_imports: If non-empty, only imports whose top-level module
            starts with one of these prefixes are permitted.  Takes
            precedence over ``denied_imports``.
        denied_imports: Imports whose top-level module starts with one of
            these prefixes are forbidden.  Ignored when ``allowed_imports``
            is non-empty.
    """

    module: str
    allowed_imports: frozenset[str] = frozenset()
    denied_imports: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Violation:
    """A single architecture boundary violation.

    Attributes:
        source_file: Path to the file containing the offending import.
        source_module: Logical module the source file belongs to.
        imported_module: The module string that was imported.
        line_number: 1-based line number of the import statement.
        rule: The boundary rule that was violated.
    """

    source_file: str
    source_module: str
    imported_module: str
    line_number: int
    rule: BoundaryRule


@dataclass(frozen=True)
class ConformanceResult:
    """Aggregate result of an architecture conformance check.

    Attributes:
        violations: All boundary violations found.
        checked_files: Number of Python files that were analysed.
        passed: True when no violations were found.
    """

    violations: tuple[Violation, ...]
    checked_files: int
    passed: bool


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def load_boundary_rules(config_path: Path | str) -> list[BoundaryRule]:
    """Parse a YAML boundary config file into a list of rules.

    The YAML must contain a top-level ``boundaries`` key with a list of
    mappings, each having ``module`` (str), and optionally
    ``allowed_imports`` (list[str]) and ``denied_imports`` (list[str]).

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        List of parsed :class:`BoundaryRule` objects.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the YAML structure is invalid.
    """
    import yaml  # lazy import — yaml is only needed when loading config

    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    data: object = yaml.safe_load(text)

    if not isinstance(data, dict):
        msg = f"Expected a YAML mapping at top level, got {type(data).__name__}"
        raise ValueError(msg)

    data_dict = cast("dict[str, Any]", data)
    boundaries_raw: object = data_dict.get("boundaries")
    if boundaries_raw is None:
        msg = "Missing required top-level key 'boundaries'"
        raise ValueError(msg)

    if not isinstance(boundaries_raw, list):
        msg = f"'boundaries' must be a list, got {type(boundaries_raw).__name__}"
        raise ValueError(msg)

    boundaries = cast("list[Any]", boundaries_raw)
    rules: list[BoundaryRule] = []
    for i, entry in enumerate(boundaries):
        if not isinstance(entry, dict):
            msg = f"boundaries[{i}]: expected a mapping, got {type(entry).__name__}"
            raise ValueError(msg)

        entry_dict = cast("dict[str, Any]", entry)
        module: object = entry_dict.get("module")
        if not module or not isinstance(module, str):
            msg = f"boundaries[{i}]: 'module' must be a non-empty string"
            raise ValueError(msg)

        allowed_raw: object = entry_dict.get("allowed_imports", [])
        denied_raw: object = entry_dict.get("denied_imports", [])

        if not isinstance(allowed_raw, list):
            msg = f"boundaries[{i}]: 'allowed_imports' must be a list"
            raise ValueError(msg)

        if not isinstance(denied_raw, list):
            msg = f"boundaries[{i}]: 'denied_imports' must be a list"
            raise ValueError(msg)

        allowed_list = cast("list[str]", allowed_raw)
        denied_list = cast("list[str]", denied_raw)

        rules.append(
            BoundaryRule(
                module=module,
                allowed_imports=frozenset(allowed_list),
                denied_imports=frozenset(denied_list),
            )
        )

    return rules


# ---------------------------------------------------------------------------
# AST import extraction
# ---------------------------------------------------------------------------


def _extract_imports(source: str) -> list[tuple[str, int]]:
    """Extract all import module names and line numbers from Python source.

    Handles both ``import X`` and ``from X import Y`` forms.

    Args:
        source: Python source code text.

    Returns:
        List of ``(module_name, line_number)`` tuples.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        logger.debug("SyntaxError while parsing source, skipping import extraction")
        return []

    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.module, node.lineno))

    return imports


def _file_to_module(file_path: Path, project_root: Path) -> str | None:
    """Convert a file path to a dotted module name relative to project root.

    Walks up from the file looking for a ``src/`` directory or the project
    root to determine the package base.

    Args:
        file_path: Absolute or relative path to a ``.py`` file.
        project_root: Root directory of the project.

    Returns:
        Dotted module string, or None if the file is not under a
        recognisable package structure.
    """
    try:
        rel = file_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None

    parts = list(rel.parts)

    # Strip leading "src/" if present
    if parts and parts[0] == "src":
        parts = parts[1:]

    if not parts:
        return None

    # Remove .py extension from last part
    last = parts[-1]
    if last.endswith(".py"):
        parts[-1] = last[:-3]
    else:
        return None

    # Drop __init__ — the package itself is the module
    if parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        return None

    return ".".join(parts)


def _find_matching_rule(module_name: str, rules: Sequence[BoundaryRule]) -> BoundaryRule | None:
    """Find the most specific rule whose module prefix matches ``module_name``.

    When multiple rules match (e.g. ``bernstein`` and ``bernstein.core``),
    the longest (most specific) match wins.

    Args:
        module_name: Dotted module name of the source file.
        rules: Available boundary rules.

    Returns:
        The matching rule, or None.
    """
    best: BoundaryRule | None = None
    best_len = -1
    for rule in rules:
        if (module_name == rule.module or module_name.startswith(rule.module + ".")) and len(rule.module) > best_len:
            best = rule
            best_len = len(rule.module)
    return best


# ---------------------------------------------------------------------------
# Per-file checking
# ---------------------------------------------------------------------------


def check_file_conformance(
    file_path: Path | str,
    rules: Sequence[BoundaryRule],
    *,
    project_root: Path | str | None = None,
) -> list[Violation]:
    """AST-parse a Python file and check its imports against boundary rules.

    Args:
        file_path: Path to the Python source file to check.
        rules: Boundary rules to enforce.
        project_root: Project root used to resolve the file's logical
            module name.  Defaults to ``file_path.parent``.

    Returns:
        List of violations found (empty if the file is clean or does not
        belong to any declared module).
    """
    fpath = Path(file_path)
    root = Path(project_root) if project_root is not None else fpath.parent

    module_name = _file_to_module(fpath, root)
    if module_name is None:
        return []

    rule = _find_matching_rule(module_name, list(rules))
    if rule is None:
        return []

    try:
        source = fpath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Cannot read %s: %s", fpath, exc)
        return []

    imports = _extract_imports(source)
    violations: list[Violation] = []

    for imported_module, lineno in imports:
        violation = _check_import_against_rule(
            imported_module=imported_module,
            source_file=str(fpath),
            source_module=module_name,
            line_number=lineno,
            rule=rule,
        )
        if violation is not None:
            violations.append(violation)

    return violations


def _check_import_against_rule(
    *,
    imported_module: str,
    source_file: str,
    source_module: str,
    line_number: int,
    rule: BoundaryRule,
) -> Violation | None:
    """Check a single import against a boundary rule.

    Args:
        imported_module: The module being imported.
        source_file: Path string of the source file.
        source_module: Logical module of the source file.
        line_number: Line number of the import statement.
        rule: The boundary rule to check against.

    Returns:
        A Violation if the import is not allowed, else None.
    """
    # allowed_imports takes precedence: if set, import must match a prefix
    if rule.allowed_imports:
        if any(
            imported_module == prefix or imported_module.startswith(prefix + ".") for prefix in rule.allowed_imports
        ):
            return None
        return Violation(
            source_file=source_file,
            source_module=source_module,
            imported_module=imported_module,
            line_number=line_number,
            rule=rule,
        )

    # denied_imports: matching prefixes are forbidden
    for denied in rule.denied_imports:
        if imported_module == denied or imported_module.startswith(denied + "."):
            return Violation(
                source_file=source_file,
                source_module=source_module,
                imported_module=imported_module,
                line_number=line_number,
                rule=rule,
            )

    return None


# ---------------------------------------------------------------------------
# Project-wide checking
# ---------------------------------------------------------------------------


def check_project_conformance(
    project_root: Path | str,
    rules: Sequence[BoundaryRule],
) -> ConformanceResult:
    """Check all Python files under ``project_root`` against boundary rules.

    Recursively walks ``project_root`` for ``.py`` files, resolves each
    file's logical module, and validates imports against the matching rule.

    Args:
        project_root: Root directory of the project to scan.
        rules: Boundary rules to enforce.

    Returns:
        Aggregate :class:`ConformanceResult`.
    """
    root = Path(project_root)
    all_violations: list[Violation] = []
    checked = 0

    for py_file in sorted(root.rglob("*.py")):
        # Skip hidden directories and common non-source dirs
        parts = py_file.relative_to(root).parts
        if any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue

        checked += 1
        file_violations = check_file_conformance(py_file, rules, project_root=root)
        all_violations.extend(file_violations)

    violations_tuple = tuple(all_violations)
    return ConformanceResult(
        violations=violations_tuple,
        checked_files=checked,
        passed=len(violations_tuple) == 0,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_conformance_report(result: ConformanceResult) -> str:
    """Render a Markdown report of architecture conformance results.

    Args:
        result: The conformance result to render.

    Returns:
        Markdown-formatted report string.
    """
    lines: list[str] = []
    lines.append("# Architecture Conformance Report")
    lines.append("")

    if result.passed:
        lines.append(f"**PASSED** - {result.checked_files} files checked, no violations found.")
        return "\n".join(lines)

    count = len(result.violations)
    lines.append(f"**FAILED** - {count} violation(s) found across {result.checked_files} files checked.")
    lines.append("")
    lines.append("## Violations")
    lines.append("")

    # Group violations by source module
    by_module: dict[str, list[Violation]] = {}
    for v in result.violations:
        by_module.setdefault(v.source_module, []).append(v)

    for module_name in sorted(by_module):
        module_violations = by_module[module_name]
        lines.append(f"### Module: `{module_name}`")
        lines.append("")
        lines.append("| File | Line | Imported Module | Rule Module |")
        lines.append("|------|------|-----------------|-------------|")
        for v in sorted(module_violations, key=lambda v: (v.source_file, v.line_number)):
            lines.append(f"| `{v.source_file}` | {v.line_number} | `{v.imported_module}` | `{v.rule.module}` |")
        lines.append("")

    lines.append("---")
    lines.append(f"*Checked {result.checked_files} files.*")

    return "\n".join(lines)
