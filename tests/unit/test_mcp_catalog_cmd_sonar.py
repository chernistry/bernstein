"""Regression tests for MCP catalog command static-analysis findings."""

from __future__ import annotations

import ast
from pathlib import Path

MODULE = Path("src/bernstein/cli/commands/mcp_catalog_cmd.py")


def test_install_cmd_does_not_keep_redundant_else_after_abort_return() -> None:
    """Install success rendering should not live behind a redundant else."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    install_cmd = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "install_cmd"
    )
    installed_none_branches = [
        node
        for node in ast.walk(install_cmd)
        if isinstance(node, ast.If) and ast.unparse(node.test) == "outcome.installed is None"
    ]

    assert len(installed_none_branches) == 1
    assert installed_none_branches[0].orelse == []
