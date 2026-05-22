"""Meta-tests for the autouse no-network guard in ``tests/unit/conftest.py``.

The guard exists to make a whole class of flaky tests impossible: unit tests
that open a real outbound (non-loopback) connection. Such a test passes when
the remote host happens to answer and fails intermittently when it does not
(for example a transient 404 / DNS hiccup), which previously wedged the merge
queue.

These tests assert the guard is actually wired up:

* a connection attempt to a non-loopback host raises the guard error;
* loopback connections are still permitted, so the many unit tests that spin a
  local mock server keep working.
"""

from __future__ import annotations

import socket

import pytest

from tests.unit._no_network import block_network, is_loopback_address


def test_guard_blocks_non_loopback_connection() -> None:
    """Opening a socket to a non-loopback host raises the guard RuntimeError."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"unit tests must not touch the network"):
            # 198.51.100.0/24 is TEST-NET-2 (RFC 5737), reserved for docs and
            # guaranteed never to route, so even a disabled guard could not
            # accidentally succeed against a real host here.
            sock.connect(("198.51.100.1", 80))
    finally:
        sock.close()


def test_guard_blocks_non_loopback_connect_ex() -> None:
    """connect_ex is guarded too (used by some clients and port probes)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"unit tests must not touch the network"):
            sock.connect_ex(("198.51.100.1", 80))
    finally:
        sock.close()


def test_guard_error_names_the_blocked_host() -> None:
    """The guard error identifies the host:port so the fix is obvious."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"blocked connection to example\.invalid:443"):
            sock.connect(("example.invalid", 443))
    finally:
        sock.close()


def test_guard_allows_loopback_ipv4() -> None:
    """Loopback connections are permitted (local mock servers must still work).

    We bind a real listener on 127.0.0.1 and connect to it; the guard must let
    this through. The connection itself succeeding proves loopback is allowed.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        # Must not raise; loopback is on the allow-list.
        client.connect(("127.0.0.1", port))
    finally:
        client.close()
        server.close()


def test_guard_allows_localhost_hostname() -> None:
    """The literal hostname ``localhost`` is treated as loopback."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        # Resolving "localhost" yields a loopback address; the guard inspects
        # the literal target before any name resolution and allows it.
        client.connect(("localhost", port))
    finally:
        client.close()
        server.close()


@pytest.mark.allow_network
def test_allow_network_marker_disables_guard() -> None:
    """``@pytest.mark.allow_network`` lets a test bypass the guard.

    The opt-out path is for the rare genuine integration test. Connecting to
    TEST-NET-2 here is expected to FAIL at the OS layer (timeout / unreachable)
    rather than raise the guard's ``RuntimeError`` - which is exactly the point:
    with the marker present, the guard no longer intercepts the call.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.05)
    try:
        with pytest.raises(OSError) as excinfo:
            sock.connect(("198.51.100.1", 80))
        assert "must not touch the network" not in str(excinfo.value)
    finally:
        sock.close()


def test_nested_block_network_keeps_guard_active_after_inner_exit() -> None:
    """Exiting a nested ``block_network()`` must not re-enable real egress.

    The autouse fixture already holds the guard for this test. Entering a
    second ``block_network()`` and leaving it must restore the *guard*, not the
    genuine socket method - otherwise the remainder of the test (and any later
    code in the same outer scope) could open a real connection. A naive
    save/restore of the import-time methods would regress here.
    """
    # Sanity: the autouse guard is active right now.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"must not touch the network"):
            sock.connect(("198.51.100.1", 80))
    finally:
        sock.close()

    # Nest and exit an inner guard.
    with block_network():
        pass

    # The guard must STILL be active after the inner context exits.
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=r"must not touch the network"):
            sock2.connect(("198.51.100.1", 80))
    finally:
        sock2.close()


@pytest.mark.parametrize(
    "address",
    [
        ("127.0.0.1", 8000),
        ("127.0.0.5", 80),
        ("::1", 443),
        ("localhost", 8080),
        ("", 0),
        "/tmp/some.sock",  # Unix-domain socket path - local IPC, always allowed
    ],
)
def test_is_loopback_address_allows_local(address: object) -> None:
    assert is_loopback_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        ("198.51.100.1", 80),
        ("example.com", 443),
        ("8.8.8.8", 53),
        ("169.254.1.1", 80),  # link-local is not loopback
    ],
)
def test_is_loopback_address_blocks_remote(address: object) -> None:
    assert is_loopback_address(address) is False
