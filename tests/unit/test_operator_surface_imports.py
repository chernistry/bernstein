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
    "bernstein.core.sandbox",
    "bernstein.core.storage.sinks",
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
    """CLI, TUI, evolution, and optional backend modules must import cleanly."""
    for root_name in _IMPORT_ROOTS:
        for module_name in _iter_modules(root_name):
            importlib.import_module(module_name)
