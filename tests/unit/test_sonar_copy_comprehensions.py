"""Regression tests for copy-only comprehensions tracked by Sonar."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_FILES = (
    Path("src/bernstein/cli/commands/cost.py"),
    Path("src/bernstein/cli/ui.py"),
    Path("src/bernstein/core/agents/multimodal_attestation.py"),
    Path("src/bernstein/core/config/home.py"),
    Path("src/bernstein/core/observability/decision_log.py"),
    Path("src/bernstein/core/planning/spec_quality.py"),
    Path("src/bernstein/core/routes/tracker_webhooks.py"),
    Path("src/bernstein/core/routes/webhooks.py"),
    Path("src/bernstein/core/routing/bandit_router.py"),
    Path("src/bernstein/sdd/validator.py"),
)


def _same_name(left: ast.AST, right: ast.AST) -> bool:
    return isinstance(left, ast.Name) and isinstance(right, ast.Name) and left.id == right.id


def _is_tuple_name_copy(left: ast.AST, right: ast.AST) -> bool:
    return (
        isinstance(left, ast.Tuple)
        and isinstance(right, ast.Tuple)
        and len(left.elts) == len(right.elts)
        and all(_same_name(l_item, r_item) for l_item, r_item in zip(left.elts, right.elts, strict=True))
    )


def _single_plain_generator(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
) -> ast.comprehension | None:
    if len(node.generators) != 1:
        return None
    generator = node.generators[0]
    return None if generator.ifs or generator.is_async else generator


def _is_copy_only(node: ast.AST) -> bool:
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        generator = _single_plain_generator(node)
        return generator is not None and _same_name(node.elt, generator.target)
    if isinstance(node, ast.DictComp):
        generator = _single_plain_generator(node)
        return generator is not None and _is_tuple_name_copy(
            ast.Tuple(elts=[node.key, node.value]),
            generator.target,
        )
    return False


def test_target_files_do_not_use_copy_only_comprehensions() -> None:
    """Use constructors for plain collection copies."""
    findings: list[tuple[Path, int, str]] = []
    for path in TARGET_FILES:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_copy_only(node):
                findings.append((path, node.lineno, ast.unparse(node)))

    assert findings == []
