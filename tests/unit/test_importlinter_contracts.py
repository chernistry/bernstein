"""Structural tests for import-linter architecture contracts."""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTLINTER_CONFIG = REPO_ROOT / ".importlinter"
ADAPTER_REGISTRY = REPO_ROOT / "src" / "bernstein" / "adapters" / "registry.py"


def _config_values(section: str, option: str) -> set[str]:
    parser = configparser.ConfigParser()
    parser.read(IMPORTLINTER_CONFIG)
    raw = parser.get(section, option)
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _registered_adapter_modules() -> set[str]:
    tree = ast.parse(ADAPTER_REGISTRY.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not node.module.startswith("bernstein.adapters."):
            continue
        if node.module == "bernstein.adapters.base":
            continue
        modules.add(node.module)

    return modules


def test_adapter_independence_contract_covers_registered_adapters() -> None:
    """Every built-in registry adapter should be covered by the independence contract."""
    contracted_modules = _config_values("importlinter:contract:adapters-independent", "modules")

    assert contracted_modules == _registered_adapter_modules()
