"""Smoke import tests for operator-facing modules."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

_IMPORT_ROOTS = (
    "bernstein.cli.commands",
    "bernstein.cli.display",
    "bernstein.tui",
    "bernstein.evolution",
    "bernstein.core.autofix",
    "bernstein.core.config",
    "bernstein.core.fleet",
    "bernstein.core.git",
    "bernstein.core.knowledge",
    "bernstein.core.observability",
    "bernstein.core.orchestration",
    "bernstein.core.protocols",
    "bernstein.core.quality",
    "bernstein.core.routes",
    "bernstein.core.sandbox",
    "bernstein.core.security",
    "bernstein.core.storage.sinks",
    "bernstein.core.tasks",
)

_IMPORT_MODULES = (
    "bernstein.cli.advanced_cmd",
    "bernstein.cli.dashboard_actions",
    "bernstein.cli.dashboard_app",
    "bernstein.cli.dashboard_header",
    "bernstein.cli.dashboard_polling",
    "bernstein.cli.live",
    "bernstein.cli.main",
    "bernstein.cli.run_confirm",
    "bernstein.cli.status",
    "bernstein.cli.status_cmd",
    "bernstein.cli.task_cmd",
    "bernstein.cli.ui",
    "bernstein.cli.workspace_cmd",
    "bernstein.core.persistence.store_factory",
    "bernstein.core.persistence.store_postgres",
    "bernstein.eval.vcr_fixture",
)


def _iter_modules(root_name: str) -> Iterator[str]:
    root = importlib.import_module(root_name)
    yield root_name

    paths = getattr(root, "__path__", None)
    if paths is None:
        return

    for info in pkgutil.walk_packages(paths, f"{root_name}."):
        yield info.name


def test_operator_surface_modules_import_cleanly() -> None:
    """Operator-facing and runtime modules must import cleanly."""
    imported = set[str]()
    for root_name in _IMPORT_ROOTS:
        for module_name in _iter_modules(root_name):
            imported.add(module_name)
            importlib.import_module(module_name)
    for module_name in _IMPORT_MODULES:
        if module_name not in imported:
            importlib.import_module(module_name)
