"""Test mutation verification for agent-written tests.

Confirms that tests written by agents actually catch bugs by generating
AST-based mutations of the source code and checking whether the test file
detects each mutation.  Independent of ``mutation_testing.py``.
"""

from __future__ import annotations

import ast
import copy
import logging
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration & result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationVerifyConfig:
    """Settings for a single source+test mutation verification run.

    Attributes:
        source_file: Path to the source module under test.
        test_file: Path to the agent-written test file.
        min_kill_rate: Minimum fraction of mutants the tests must kill (0-1).
        timeout_per_mutant_s: Max seconds per mutant test execution.
    """

    source_file: Path
    test_file: Path
    min_kill_rate: float = 0.7
    timeout_per_mutant_s: int = 30


@dataclass(frozen=True)
class MutantOutcome:
    """Result of running the test suite against one mutant.

    Attributes:
        line: Source line number where the mutation was applied.
        mutation_type: Human-readable label for the mutation kind.
        killed: Whether the tests detected the mutation.
        original_code: The original code snippet at the mutation site.
        mutated_code: The mutated code snippet.
    """

    line: int
    mutation_type: str
    killed: bool
    original_code: str
    mutated_code: str


@dataclass(frozen=True)
class VerificationResult:
    """Aggregate result of verifying agent-written tests via mutation.

    Attributes:
        config: The configuration used for this run.
        total_mutants: Number of mutants generated.
        killed: Number of mutants detected by the tests.
        survived: Number of mutants the tests missed.
        kill_rate: Fraction of mutants killed (0.0 - 1.0).
        passed: Whether the kill rate meets the configured threshold.
        outcomes: Per-mutant details.
    """

    config: MutationVerifyConfig
    total_mutants: int
    killed: int
    survived: int
    kill_rate: float
    passed: bool
    outcomes: tuple[MutantOutcome, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# AST mutation tables
# ---------------------------------------------------------------------------

_COMPARE_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
}

_BINOP_SWAPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}


# ---------------------------------------------------------------------------
# Internal mutant representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SimpleMutant:
    """Internal representation of a single AST mutation."""

    line: int
    mutation_type: str
    mutated_source: str
    original_snippet: str
    mutated_snippet: str


# ---------------------------------------------------------------------------
# Mutant generation
# ---------------------------------------------------------------------------


def generate_simple_mutants(source_code: str) -> list[_SimpleMutant]:
    """Generate AST-based mutants from *source_code*.

    Supported mutations:
    - Comparison swap: ``==`` <-> ``!=``, ``<`` <-> ``>=``, ``>`` <-> ``<=``
    - Boolean negate: ``True`` <-> ``False``
    - Return removal: ``return <expr>`` -> ``return None``
    - Arithmetic swap: ``+`` <-> ``-``, ``*`` <-> ``/``

    Args:
        source_code: Valid Python source text.

    Returns:
        List of ``_SimpleMutant`` instances, one per generated mutation.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        logger.warning("Cannot parse source for mutation; skipping")
        return []

    mutants: list[_SimpleMutant] = []
    source_lines = source_code.splitlines()

    for node in ast.walk(tree):
        mutants.extend(_compare_mutants(tree, node, source_lines))
        mutants.extend(_bool_mutants(tree, node, source_lines))
        mutants.extend(_return_mutants(tree, node, source_lines))
        mutants.extend(_arith_mutants(tree, node, source_lines))

    return mutants


def _get_source_line(source_lines: list[str], lineno: int) -> str:
    """Safely retrieve a source line (1-indexed)."""
    if 1 <= lineno <= len(source_lines):
        return source_lines[lineno - 1].strip()
    return ""


def _compare_mutants(tree: ast.Module, node: ast.AST, source_lines: list[str]) -> list[_SimpleMutant]:
    """Generate comparison-operator swap mutants."""
    if not isinstance(node, ast.Compare):
        return []
    results: list[_SimpleMutant] = []
    for idx, op in enumerate(node.ops):
        swap_type = _COMPARE_SWAPS.get(type(op))
        if swap_type is None:
            continue
        mutated_tree = copy.deepcopy(tree)
        target = _find_compare(mutated_tree, node, idx)
        if target is None:
            continue
        target.ops[idx] = swap_type()
        try:
            new_source = ast.unparse(mutated_tree)
        except Exception:
            continue
        original_line = _get_source_line(source_lines, node.lineno)
        results.append(
            _SimpleMutant(
                line=node.lineno,
                mutation_type=f"compare_swap({type(op).__name__}->{swap_type.__name__})",
                mutated_source=new_source,
                original_snippet=original_line,
                mutated_snippet=_get_source_line(new_source.splitlines(), node.lineno),
            )
        )
    return results


def _bool_mutants(tree: ast.Module, node: ast.AST, source_lines: list[str]) -> list[_SimpleMutant]:
    """Generate boolean constant negation mutants."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, bool):
        return []
    swapped = not node.value
    mutated_tree = copy.deepcopy(tree)
    target = _find_constant(mutated_tree, node)
    if target is None:
        return []
    target.value = swapped
    try:
        new_source = ast.unparse(mutated_tree)
    except Exception:
        return []
    original_line = _get_source_line(source_lines, node.lineno)
    return [
        _SimpleMutant(
            line=node.lineno,
            mutation_type=f"bool_negate({node.value}->{swapped})",
            mutated_source=new_source,
            original_snippet=original_line,
            mutated_snippet=_get_source_line(new_source.splitlines(), node.lineno),
        )
    ]


def _return_mutants(tree: ast.Module, node: ast.AST, source_lines: list[str]) -> list[_SimpleMutant]:
    """Generate return-removal mutants (``return expr`` -> ``return None``)."""
    if not isinstance(node, ast.Return) or node.value is None:
        return []
    mutated_tree = copy.deepcopy(tree)
    target = _find_return(mutated_tree, node)
    if target is None:
        return []
    target.value = ast.Constant(value=None)
    try:
        new_source = ast.unparse(mutated_tree)
    except Exception:
        return []
    original_line = _get_source_line(source_lines, node.lineno)
    return [
        _SimpleMutant(
            line=node.lineno,
            mutation_type="return_remove",
            mutated_source=new_source,
            original_snippet=original_line,
            mutated_snippet=_get_source_line(new_source.splitlines(), node.lineno),
        )
    ]


def _arith_mutants(tree: ast.Module, node: ast.AST, source_lines: list[str]) -> list[_SimpleMutant]:
    """Generate arithmetic-operator swap mutants."""
    if not isinstance(node, ast.BinOp):
        return []
    swap_type = _BINOP_SWAPS.get(type(node.op))
    if swap_type is None:
        return []
    mutated_tree = copy.deepcopy(tree)
    target = _find_binop(mutated_tree, node)
    if target is None:
        return []
    target.op = swap_type()
    try:
        new_source = ast.unparse(mutated_tree)
    except Exception:
        return []
    original_line = _get_source_line(source_lines, node.lineno)
    return [
        _SimpleMutant(
            line=node.lineno,
            mutation_type=f"arith_swap({type(node.op).__name__}->{swap_type.__name__})",
            mutated_source=new_source,
            original_snippet=original_line,
            mutated_snippet=_get_source_line(new_source.splitlines(), node.lineno),
        )
    ]


# ---------------------------------------------------------------------------
# AST node finders
# ---------------------------------------------------------------------------


def _find_compare(tree: ast.Module, original: ast.Compare, op_idx: int) -> ast.Compare | None:
    """Locate the Compare node in *tree* matching *original* by position."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Compare)
            and node.lineno == original.lineno
            and node.col_offset == original.col_offset
            and len(node.ops) > op_idx
        ):
            return node
    return None


def _find_constant(tree: ast.Module, original: ast.Constant) -> ast.Constant | None:
    """Locate the Constant node matching *original* by position and value."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and node.lineno == original.lineno
            and node.col_offset == original.col_offset
            and node.value == original.value
        ):
            return node
    return None


def _find_return(tree: ast.Module, original: ast.Return) -> ast.Return | None:
    """Locate the Return node matching *original* by position."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.lineno == original.lineno and node.col_offset == original.col_offset:
            return node
    return None


def _find_binop(tree: ast.Module, original: ast.BinOp) -> ast.BinOp | None:
    """Locate the BinOp node matching *original* by position."""
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and node.lineno == original.lineno and node.col_offset == original.col_offset:
            return node
    return None


# ---------------------------------------------------------------------------
# Mutant execution
# ---------------------------------------------------------------------------


def run_test_against_mutant(
    test_file: Path,
    mutant_code: str,
    source_path: Path,
    timeout: int,
) -> bool:
    """Write mutant code to *source_path*, run *test_file*, and restore.

    Args:
        test_file: Path to the test file to execute.
        mutant_code: Full replacement source for the file under test.
        source_path: Path to the source file being mutated.
        timeout: Maximum seconds before the test run is killed.

    Returns:
        ``True`` if the tests detected the mutation (killed), ``False`` if
        the mutant survived (tests still pass).
    """
    backup = source_path.read_text(encoding="utf-8")
    try:
        source_path.write_text(mutant_code, encoding="utf-8")
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_file), "-x", "-q"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=source_path.parent,
        )
        return result.returncode != 0
    except subprocess.TimeoutExpired:
        return True
    except OSError as exc:
        logger.warning("OS error running test against mutant: %s", exc)
        return True
    finally:
        source_path.write_text(backup, encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def verify_agent_tests(
    source_file: Path,
    test_file: Path,
    project_root: Path,
    *,
    min_kill_rate: float = 0.7,
    timeout_per_mutant_s: int = 30,
) -> VerificationResult:
    """Run mutation verification on agent-written tests.

    Generates AST-based mutations of *source_file* and checks whether
    *test_file* detects each one.

    Args:
        source_file: Path to the source module under test.
        test_file: Path to the agent-written test file.
        project_root: Repository root directory (used for resolving paths).
        min_kill_rate: Minimum fraction of mutants the tests must kill.
        timeout_per_mutant_s: Max seconds per mutant test execution.

    Returns:
        A ``VerificationResult`` summarising the run.
    """
    config = MutationVerifyConfig(
        source_file=source_file,
        test_file=test_file,
        min_kill_rate=min_kill_rate,
        timeout_per_mutant_s=timeout_per_mutant_s,
    )

    # Resolve paths relative to project root if needed
    resolved_source = source_file if source_file.is_absolute() else project_root / source_file
    resolved_test = test_file if test_file.is_absolute() else project_root / test_file

    if not resolved_source.is_file():
        logger.warning("Source file %s not found", resolved_source)
        return VerificationResult(
            config=config,
            total_mutants=0,
            killed=0,
            survived=0,
            kill_rate=1.0,
            passed=True,
            outcomes=(),
        )

    if not resolved_test.is_file():
        logger.warning("Test file %s not found", resolved_test)
        return VerificationResult(
            config=config,
            total_mutants=0,
            killed=0,
            survived=0,
            kill_rate=0.0,
            passed=False,
            outcomes=(),
        )

    source_code = resolved_source.read_text(encoding="utf-8")
    mutants = generate_simple_mutants(source_code)

    if not mutants:
        logger.info("No mutants generated for %s", resolved_source)
        return VerificationResult(
            config=config,
            total_mutants=0,
            killed=0,
            survived=0,
            kill_rate=1.0,
            passed=True,
            outcomes=(),
        )

    outcomes: list[MutantOutcome] = []
    for mutant in mutants:
        killed = run_test_against_mutant(
            resolved_test,
            mutant.mutated_source,
            resolved_source,
            timeout_per_mutant_s,
        )
        outcomes.append(
            MutantOutcome(
                line=mutant.line,
                mutation_type=mutant.mutation_type,
                killed=killed,
                original_code=mutant.original_snippet,
                mutated_code=mutant.mutated_snippet,
            )
        )

    total = len(outcomes)
    killed_count = sum(1 for o in outcomes if o.killed)
    survived_count = total - killed_count
    kill_rate = killed_count / total if total > 0 else 1.0
    kill_rate = round(kill_rate, 4)

    return VerificationResult(
        config=config,
        total_mutants=total,
        killed=killed_count,
        survived=survived_count,
        kill_rate=kill_rate,
        passed=kill_rate >= min_kill_rate,
        outcomes=tuple(outcomes),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_verification_report(result: VerificationResult) -> str:
    """Render a verification result as Markdown.

    Args:
        result: Completed verification result.

    Returns:
        Markdown string suitable for display or file output.
    """
    status = "PASS" if result.passed else "FAIL"
    lines: list[str] = [
        "# Test Mutation Verification Report",
        "",
        f"**Source:** `{result.config.source_file}`",
        f"**Tests:** `{result.config.test_file}`",
        f"**Kill rate:** {result.kill_rate:.0%} ({result.killed}/{result.total_mutants})",
        f"**Threshold:** {result.config.min_kill_rate:.0%}",
        f"**Result:** {status}",
        "",
    ]

    survived = [o for o in result.outcomes if not o.killed]
    if survived:
        lines.append("## Surviving Mutants")
        lines.append("")
        lines.append("| Line | Mutation | Original | Mutated |")
        lines.append("|------|----------|----------|---------|")
        for o in survived:
            orig = _escape_pipe(o.original_code)
            mut = _escape_pipe(o.mutated_code)
            lines.append(f"| {o.line} | {o.mutation_type} | `{orig}` | `{mut}` |")
        lines.append("")

    killed_outcomes = [o for o in result.outcomes if o.killed]
    if killed_outcomes:
        lines.append(f"## Killed Mutants ({len(killed_outcomes)})")
        lines.append("")
        lines.append("| Line | Mutation |")
        lines.append("|------|----------|")
        for o in killed_outcomes:
            lines.append(f"| {o.line} | {o.mutation_type} |")
        lines.append("")

    return "\n".join(lines)


def _escape_pipe(text: str) -> str:
    """Escape pipe characters for Markdown table cells."""
    return text.replace("|", "\\|")
