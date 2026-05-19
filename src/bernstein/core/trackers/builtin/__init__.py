"""Built-in tracker adapters."""

from __future__ import annotations

from bernstein.core.trackers.builtin.clickup_adapter import (
    ClickUpAdapter,
    ClickUpConfig,
)
from bernstein.core.trackers.builtin.github_projects_adapter import (
    GitHubProjectsV2Adapter,
    GitHubProjectsV2Config,
)

__all__ = [
    "ClickUpAdapter",
    "ClickUpConfig",
    "GitHubProjectsV2Adapter",
    "GitHubProjectsV2Config",
]
