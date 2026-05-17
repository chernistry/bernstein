"""Integration tests for the citation verifier against a fixture HTTP server.

A small in-process ``http.server.HTTPServer`` impersonates the public
endpoints (arXiv abstract, doi.org redirector, github issue page, plus
a couple of synthetic 200/404/405 routes) so the verifier's online code
paths can be exercised without touching the real internet.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from bernstein.core.quality import citation_verifier as cv
from bernstein.core.quality.citation_verifier import verify_citations


class _FixtureHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler with a routing table the verifier can exercise."""

    # Map of "<method> <path>" -> (status, body).
    routes: ClassVar[dict[str, tuple[int, bytes]]] = {
        "HEAD /ok": (200, b""),
        "GET /ok": (200, b"ok"),
        "HEAD /redirect": (301, b""),
        "GET /redirect": (301, b""),
        "HEAD /missing": (404, b""),
        "GET /missing": (404, b"missing"),
        "HEAD /head-not-allowed": (405, b""),
        "GET /head-not-allowed": (200, b"ok"),
        "HEAD /server-error": (500, b""),
        "GET /server-error": (500, b"oops"),
    }

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def _dispatch(self, method: str) -> None:
        key = f"{method} {self.path}"
        status, body = self.routes.get(key, (404, b"missing"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return  # silence stderr noise during tests


@pytest.fixture
def http_server() -> Iterator[str]:
    """Spin up the fixture HTTP server on an ephemeral port.

    Yields the base URL (e.g. ``http://127.0.0.1:54321``).
    """
    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        with closing(server.socket):
            pass
        thread.join(timeout=2)


def test_integration_200_resolves(http_server: str) -> None:
    report = verify_citations(f"see {http_server}/ok here")
    assert report.ok
    assert len(report.resolved) == 1


def test_integration_404_unresolved(http_server: str) -> None:
    report = verify_citations(f"see {http_server}/missing here")
    assert not report.ok
    assert len(report.unresolved) == 1


def test_integration_405_falls_back_to_get_resolves(http_server: str) -> None:
    report = verify_citations(f"see {http_server}/head-not-allowed here")
    assert report.ok
    assert len(report.resolved) == 1


def test_integration_3xx_redirect_resolves(http_server: str) -> None:
    report = verify_citations(f"see {http_server}/redirect here")
    assert report.ok
    assert len(report.resolved) == 1


def test_integration_5xx_unresolved(http_server: str) -> None:
    report = verify_citations(f"see {http_server}/server-error here")
    assert not report.ok
    assert len(report.unresolved) == 1


def test_integration_mixed_artefact(http_server: str) -> None:
    text = (
        f"resolved at {http_server}/ok"
        f" but {http_server}/missing failed"
        f" and {http_server}/head-not-allowed worked after fallback"
    )
    report = verify_citations(text)
    assert report.total == 3
    assert len(report.resolved) == 2
    assert len(report.unresolved) == 1


def test_integration_short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wire a deliberately tiny timeout and an unreachable server.
    monkeypatch.setattr(cv, "_HTTP_TIMEOUT_S", 0.001)
    # 198.51.100.1 is from TEST-NET-2 (RFC 5737) -- guaranteed
    # non-routable.
    report = verify_citations("see https://198.51.100.1/x")
    assert not report.ok
