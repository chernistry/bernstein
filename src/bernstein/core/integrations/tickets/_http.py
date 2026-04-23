"""Tiny HTTP helper shared by ticket providers.

Each provider module (Linear GraphQL, GitHub Issues REST, Jira REST)
needs the same two-layer fetch: prefer ``httpx`` when available, fall
back to ``urllib.request`` otherwise, and translate 401/403 into
:class:`TicketAuthError` and any other 4xx/5xx into :class:`TicketParseError`.
Centralising the shape removes the copy-paste between providers.
"""

from __future__ import annotations

import json
from typing import Any, cast

from bernstein.core.integrations.tickets import TicketAuthError, TicketParseError

DEFAULT_TIMEOUT_SECONDS = 10.0


def http_get_json(
    *,
    url: str,
    headers: dict[str, str],
    provider_label: str,
    auth_env_var: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """GET ``url`` and decode the JSON body.

    Args:
        url: Full endpoint URL.
        headers: HTTP request headers.
        provider_label: Human-readable provider name used in error messages
            (e.g. ``"GitHub"``, ``"Jira"``).
        auth_env_var: Name of the environment variable users should check
            when they hit a 401/403.
        timeout: Per-call timeout in seconds.

    Returns:
        The decoded JSON object.

    Raises:
        TicketAuthError: The provider replied 401 or 403.
        TicketParseError: Any other 4xx / 5xx response.
    """
    try:
        import httpx

        resp = httpx.get(url, headers=headers, timeout=timeout)
        if resp.status_code in (401, 403):
            raise TicketAuthError(
                f"{provider_label} rejected the request (HTTP {resp.status_code}). "
                f"Check the {auth_env_var} environment variable."
            )
        if resp.status_code >= 400:
            raise TicketParseError(
                f"{provider_label} API returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return cast(dict[str, Any], resp.json())
    except ImportError:  # pragma: no cover - httpx is a declared dependency
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as handle:
                return cast(dict[str, Any], json.loads(handle.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise TicketAuthError(
                    f"{provider_label} rejected the request (HTTP {exc.code}). "
                    f"Check the {auth_env_var} environment variable."
                ) from exc
            raise TicketParseError(
                f"{provider_label} API returned HTTP {exc.code}"
            ) from exc
