"""GitHub App integration layer for Bernstein.

Provides webhook parsing, HMAC signature verification, and event-to-task
mapping so that GitHub events (issues, PRs, pushes) can be automatically
converted into Bernstein tasks.
"""

from bernstein.github_app.mapper import issue_to_tasks, pr_comment_to_task, push_to_tasks
from bernstein.github_app.webhooks import WebhookEvent, parse_webhook, verify_signature

__all__ = [
    "WebhookEvent",
    "issue_to_tasks",
    "parse_webhook",
    "pr_comment_to_task",
    "push_to_tasks",
    "verify_signature",
]
