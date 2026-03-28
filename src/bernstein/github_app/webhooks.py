"""GitHub App webhook parsing and signature verification.

Parses raw HTTP webhook requests from GitHub into typed ``WebhookEvent``
objects, and verifies the HMAC-SHA256 signature that GitHub includes in
every delivery.

Signature format:  ``X-Hub-Signature-256: sha256=<hex-digest>``
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WebhookEvent:
    """A parsed GitHub webhook delivery.

    Attributes:
        event_type: GitHub event name from ``X-GitHub-Event`` header
            (e.g. ``"issues"``, ``"pull_request"``, ``"push"``,
            ``"issue_comment"``).
        action: Action field from the payload body (e.g. ``"opened"``,
            ``"closed"``, ``"synchronize"``). Empty string when the event
            has no ``action`` field (e.g. ``"push"``).
        repo: Full repository name in ``"owner/repo"`` format, sourced
            from ``payload["repository"]["full_name"]``.
        payload: Raw decoded JSON payload as a dict.
    """

    event_type: str
    action: str
    repo: str
    payload: dict[str, Any]


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the HMAC-SHA256 signature GitHub attaches to every delivery.

    GitHub computes ``HMAC-SHA256(secret, body)`` and sends it as
    ``X-Hub-Signature-256: sha256=<hex>``.  This function recomputes the
    digest and performs a constant-time comparison to prevent timing attacks.

    Args:
        body: Raw request body bytes exactly as received (before any decoding).
        signature: Value of the ``X-Hub-Signature-256`` header, including the
            ``"sha256="`` prefix.
        secret: The webhook secret configured in the GitHub App settings.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise (including
        when the signature header is missing or malformed).
    """
    if not signature.startswith("sha256="):
        return False
    expected_hex = signature[len("sha256=") :]
    mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), expected_hex)


def parse_webhook(headers: dict[str, str], body: bytes) -> WebhookEvent:
    """Parse a raw GitHub webhook request into a :class:`WebhookEvent`.

    Header lookup is case-insensitive to tolerate both the canonical
    ``X-GitHub-Event`` form and lowercased variants produced by some
    HTTP frameworks.

    Args:
        headers: HTTP request headers as a plain dict.  May use any case
            for key names.
        body: Raw request body bytes.

    Returns:
        A fully populated :class:`WebhookEvent`.

    Raises:
        ValueError: If the ``X-GitHub-Event`` header is missing, the body
            is not valid JSON, or ``repository.full_name`` cannot be found
            in the payload.
    """
    # Normalise header keys to lowercase for case-insensitive lookup.
    lower_headers = {k.lower(): v for k, v in headers.items()}

    event_type = lower_headers.get("x-github-event", "").strip()
    if not event_type:
        raise ValueError("Missing required header: X-GitHub-Event")

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Webhook body is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object")

    repo_obj = payload.get("repository")
    if not isinstance(repo_obj, dict):
        raise ValueError("Payload missing 'repository' object")
    repo = repo_obj.get("full_name", "")
    if not repo:
        raise ValueError("Payload missing 'repository.full_name'")

    action: str = payload.get("action") or ""

    return WebhookEvent(
        event_type=event_type,
        action=action,
        repo=repo,
        payload=payload,
    )
