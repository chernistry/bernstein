"""Regression tests for chat driver cancellation handlers."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
CHAT_DRIVER_PATHS = (
    Path("src/bernstein/core/chat/drivers/discord.py"),
    Path("src/bernstein/core/chat/drivers/slack.py"),
    Path("src/bernstein/core/chat/drivers/telegram.py"),
)


def test_chat_drivers_do_not_catch_cancelled_error_only_to_rethrow() -> None:
    """CancelledError should propagate without a redundant handler."""
    redundant_handlers: list[tuple[Path, int]] = []
    for path in CHAT_DRIVER_PATHS:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        for handler in ast.walk(tree):
            if (
                isinstance(handler, ast.ExceptHandler)
                and isinstance(handler.type, ast.Attribute)
                and handler.type.attr == "CancelledError"
                and len(handler.body) == 1
                and isinstance(handler.body[0], ast.Raise)
                and handler.body[0].exc is None
            ):
                redundant_handlers.append((path, handler.lineno))

    assert redundant_handlers == []
