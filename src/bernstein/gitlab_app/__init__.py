"""GitLab App integration for Bernstein.

Receives GitLab webhooks, converts events to Bernstein tasks, and posts
them to the task server.  Provides webhook verification (constant-time
token comparison), event parsing, event-to-task mapping, and pipeline
status updates.

Mirror of :mod:`bernstein.github_app`.  Self-managed GitLab installs can
override the base URL via the ``BERNSTEIN_GITLAB_URL`` environment
variable (defaults to ``https://gitlab.com``).
"""

from __future__ import annotations

from bernstein.gitlab_app.app import GitLabAppConfig, get_gitlab_base_url
from bernstein.gitlab_app.mapper import (
    merge_request_to_tasks,
    note_to_task,
    pipeline_to_tasks,
)
from bernstein.gitlab_app.webhooks import GitLabWebhookEvent, parse_webhook, verify_token

__all__ = [
    "GitLabAppConfig",
    "GitLabWebhookEvent",
    "get_gitlab_base_url",
    "merge_request_to_tasks",
    "note_to_task",
    "parse_webhook",
    "pipeline_to_tasks",
    "verify_token",
]
