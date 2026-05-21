"""Perf-suite conftest - ensure local src/ takes priority on sys.path.

Mirrors ``tests/unit/conftest.py``: in git worktrees a parent project's
venv may appear earlier on sys.path than the worktree's own ``src/``,
shadowing newly added modules. Insert ``src/`` at position 0 so imports
always resolve to the locally checked-out code.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
