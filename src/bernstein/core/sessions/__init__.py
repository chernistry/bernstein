"""Session-level orchestration primitives that span multiple subsystems.

The :mod:`fork` submodule lets an operator clone a recorded session into a
sibling git worktree to explore an alternate path without disturbing the
parent.  See :func:`bernstein.core.sessions.fork.fork_session`.
"""

from __future__ import annotations

from bernstein.core.sessions.fork import (
    SessionFork,
    SessionForkError,
    fork_session,
)

__all__ = [
    "SessionFork",
    "SessionForkError",
    "fork_session",
]
