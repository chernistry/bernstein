"""URL scheme allow-listing for outbound HTTP requests.

This module exists to lock down operator-supplied URLs passed to
``urllib.request.urlopen`` so they cannot be coerced into reading local
files (``file://``), launching FTP transfers, or being interpreted as some
other scheme that ``urllib`` happens to support out of the box.

The Semgrep rule
``python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected``
flags every dynamic ``urlopen`` call site because of exactly that risk. By
piping operator-supplied URLs through :func:`ensure_http_url` first we get a
defence-in-depth check independent of the per-call-site allow-list comments.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlparse

__all__ = ["UrlSchemeError", "ensure_http_url"]


class UrlSchemeError(ValueError):
    """Raised when a URL is rejected by :func:`ensure_http_url`."""


_HTTPS_ONLY: Final[frozenset[str]] = frozenset({"https"})
_HTTP_AND_HTTPS: Final[frozenset[str]] = frozenset({"http", "https"})
# Hosts that always permit plain HTTP. ``0.0.0.0`` is intentionally NOT
# included: it is the "bind-any" address, not a loopback target, and
# treating it as loopback would silently allow non-localhost HTTP in
# environments that translate ``0.0.0.0`` to a routable interface.
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})


def ensure_http_url(  # NOSONAR python:S3516 - accept path returns input unchanged; rejection is via raise
    url: str,
    *,
    allow_http: bool = False,
    source: str = "",
) -> str:
    """Validate that ``url`` has an http(s) scheme; return it unchanged.

    This is a validate-and-passthrough guard: every accept path returns
    the same value (the input ``url``), and rejection is signalled by
    raising :class:`UrlSchemeError` rather than by a sentinel return.
    That invariant return is intentional (hence the ``S3516`` waiver) and
    must not be relaxed into an always-allow.

    Args:
        url: The candidate URL string.
        allow_http: When True, accept ``http://`` URLs in addition to
            ``https://``. Even when False, plain ``http://`` is still accepted
            for localhost / loopback hosts so developers can hit local mock
            servers without flipping the flag globally.
        source: Optional human-readable label used in the error message
            (e.g. ``"jira webhook"``) for easier debugging.

    Returns:
        ``url`` if it passes validation.

    Raises:
        UrlSchemeError: If the URL is empty, unparseable, or uses any scheme
            other than the permitted ones.
    """
    if not url or not isinstance(url, str):
        raise UrlSchemeError(_msg(source, "URL is empty or not a string"))

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise UrlSchemeError(_msg(source, f"URL has no scheme: {url!r}"))

    allowed = _HTTP_AND_HTTPS if allow_http else _HTTPS_ONLY
    host = (parsed.hostname or "").lower()
    if scheme == "http" and host in _LOCAL_HOSTS:
        # Loopback hosts are always permitted on plain HTTP - most operator
        # toolchains expect to be able to point Bernstein at a local mock.
        return url
    if scheme not in allowed:
        raise UrlSchemeError(
            _msg(
                source,
                f"URL scheme {scheme!r} is not permitted (allowed: {sorted(allowed)!r}); url={url!r}",
            )
        )
    return url


def _msg(source: str, body: str) -> str:
    prefix = f"{source}: " if source else ""
    return f"{prefix}{body}"
