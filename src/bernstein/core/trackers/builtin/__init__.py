"""Built-in tracker adapters."""

from __future__ import annotations

from bernstein.core.trackers.builtin.asana_adapter import (
    AsanaAdapter,
    AsanaConfig,
)
from bernstein.core.trackers.builtin.clickup_adapter import (
    ClickUpAdapter,
    ClickUpConfig,
)
from bernstein.core.trackers.builtin.github_projects_adapter import (
    GitHubProjectsV2Adapter,
    GitHubProjectsV2Config,
)
from bernstein.core.trackers.builtin.gitlab_adapter import (
    GitLabAdapter,
    GitLabConfig,
)
from bernstein.core.trackers.builtin.jira_cloud_adapter import (
    JiraCloudConfig,
    JiraCloudTracker,
)
from bernstein.core.trackers.builtin.jira_dc_adapter import (
    JiraDataCenterAdapter,
    JiraDataCenterConfig,
)
from bernstein.core.trackers.builtin.plane_adapter import (
    PlaneAdapter,
    PlaneConfig,
)

__all__ = [
    "AsanaAdapter",
    "AsanaConfig",
    "ClickUpAdapter",
    "ClickUpConfig",
    "GitHubProjectsV2Adapter",
    "GitHubProjectsV2Config",
    "GitLabAdapter",
    "GitLabConfig",
    "JiraCloudConfig",
    "JiraCloudTracker",
    "JiraDataCenterAdapter",
    "JiraDataCenterConfig",
    "PlaneAdapter",
    "PlaneConfig",
]
