"""Tests for :func:`bernstein.core.security.ensure_http_url`.

``ensure_http_url`` is a validate-and-passthrough guard: every call site
relies on it returning its input unchanged on accept and raising
:class:`UrlSchemeError` on reject. The function is marked ``NOSONAR
python:S3516`` because every accept path returns the same value (the
input ``url``); these tests pin the *reject* paths so the suppression
can never mask a regression that turns the guard into an always-allow.
"""

from __future__ import annotations

import pytest

from bernstein.core.security.url_allowlist import UrlSchemeError, ensure_http_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path",
        "https://example.com",
        "https://localhost:8052/health",
    ],
)
def test_https_is_accepted_and_returned_unchanged(url: str) -> None:
    assert ensure_http_url(url) == url


def test_http_rejected_by_default() -> None:
    with pytest.raises(UrlSchemeError):
        ensure_http_url("http://example.com")


def test_http_accepted_when_allow_http_set() -> None:
    url = "http://example.com/webhook"
    assert ensure_http_url(url, allow_http=True) == url


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "[::1]"],
)
def test_loopback_http_always_allowed(host: str) -> None:
    """Plain HTTP to loopback is permitted even without ``allow_http``.

    IPv6 literals must be bracketed in a URL authority so ``urlparse``
    extracts ``::1`` as the hostname rather than mis-splitting on the
    colons.
    """
    url = f"http://{host}:8052/mock"
    assert ensure_http_url(url) == url


def test_bind_any_host_is_not_treated_as_loopback() -> None:
    """``0.0.0.0`` is the bind-any address, not a loopback target.

    It must NOT inherit the localhost plain-HTTP exemption, otherwise a
    routable interface could receive unencrypted traffic.
    """
    with pytest.raises(UrlSchemeError):
        ensure_http_url("http://0.0.0.0:8052/x")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/payload",
        "gopher://example.com",
        "data:text/plain;base64,AAAA",
        "jar:file:///tmp/x.jar",
    ],
)
def test_non_http_schemes_are_rejected(url: str) -> None:
    """The whole point of the guard: deny non-http(s) schemes.

    A regression that made this an always-allow guard (returning the URL
    for every input) would surface here as the missing ``UrlSchemeError``.
    """
    with pytest.raises(UrlSchemeError):
        ensure_http_url(url, allow_http=True)


def test_empty_url_rejected() -> None:
    with pytest.raises(UrlSchemeError):
        ensure_http_url("")


def test_schemeless_url_rejected() -> None:
    with pytest.raises(UrlSchemeError):
        ensure_http_url("example.com/no-scheme")


def test_error_message_includes_source_label() -> None:
    with pytest.raises(UrlSchemeError, match="jira webhook"):
        ensure_http_url("ftp://example.com", source="jira webhook")
