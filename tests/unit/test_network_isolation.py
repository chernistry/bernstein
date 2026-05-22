"""Tests for SEC-014: Network isolation validation for sandboxed agents.

These are unit tests, so they must not open a real outbound connection (the
autouse guard in ``tests/unit/conftest.py`` blocks non-loopback egress). The
validator's reachability probe is exercised by patching the socket it builds so
the "unreachable" path is simulated deterministically with an ``OSError``,
rather than by relying on a documentation-range address (192.0.2.0/24) actually
failing to route - which is exactly the flaky-by-network behaviour the guard
exists to forbid.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from bernstein.core.security.network_isolation import (
    CheckStatus,
    Endpoint,
    IsolationLevel,
    NetworkIsolationValidator,
    NetworkPolicy,
)


@contextmanager
def _socket_connect_raises(exc: OSError) -> Iterator[MagicMock]:
    """Patch the validator's socket so ``connect`` raises ``exc``.

    Simulates an unreachable endpoint without any real network egress. The
    validator builds the socket via ``socket.socket(...)`` in the
    ``network_isolation`` module, so we patch that factory.
    """
    fake_sock = MagicMock(name="socket")
    fake_sock.connect.side_effect = exc
    with patch("bernstein.core.security.network_isolation.socket.socket", return_value=fake_sock) as factory:
        yield factory


@contextmanager
def _socket_connect_succeeds() -> Iterator[MagicMock]:
    """Patch the validator's socket so ``connect`` succeeds (reachable)."""
    fake_sock = MagicMock(name="socket")
    fake_sock.connect.return_value = None
    with patch("bernstein.core.security.network_isolation.socket.socket", return_value=fake_sock) as factory:
        yield factory


@contextmanager
def _dns_resolves(resolves: bool) -> Iterator[MagicMock]:
    """Patch ``getaddrinfo`` so DNS resolution is simulated, not performed.

    The DNS check issues a real ``socket.getaddrinfo`` lookup; pin it so the
    test neither depends on a resolver nor performs egress (the guard blocks
    connections, not DNS, so without this the lookup would still leave the
    sandbox).
    """
    target = "bernstein.core.security.network_isolation.socket.getaddrinfo"
    if resolves:
        with patch(target, return_value=[(2, 1, 6, "", ("203.0.113.1", 0))]) as gai:
            yield gai
    else:
        import socket as _socket

        with patch(target, side_effect=_socket.gaierror("simulated: name resolution disabled")) as gai:
            yield gai


class TestEndpoint:
    def test_str(self) -> None:
        ep = Endpoint(host="127.0.0.1", port=8052)
        assert str(ep) == "127.0.0.1:8052"

    def test_frozen(self) -> None:
        ep = Endpoint(host="localhost", port=80)
        assert ep.host == "localhost"
        assert ep.port == 80


class TestNetworkPolicy:
    def test_defaults(self) -> None:
        policy = NetworkPolicy()
        assert policy.isolation_level == IsolationLevel.RESTRICTED
        assert not policy.dns_allowed
        assert policy.timeout_seconds == 2.0

    def test_custom_policy(self) -> None:
        policy = NetworkPolicy(
            isolation_level=IsolationLevel.LOCAL_ONLY,
            allowed_endpoints=(Endpoint("127.0.0.1", 8052),),
            denied_endpoints=(Endpoint("8.8.8.8", 53),),
            dns_allowed=False,
        )
        assert len(policy.allowed_endpoints) == 1
        assert len(policy.denied_endpoints) == 1


class TestNetworkIsolationValidator:
    def test_blocked_endpoint_passes(self) -> None:
        """An endpoint that is unreachable should pass the 'blocked' check."""
        policy = NetworkPolicy(
            denied_endpoints=(Endpoint("192.0.2.1", 1),),
            timeout_seconds=0.5,
        )
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_raises(OSError("simulated: connection refused")):
            check = validator.check_endpoint_blocked(Endpoint("192.0.2.1", 1))
        # connect() raised, so the endpoint is correctly reported as blocked.
        assert check.status == CheckStatus.PASS

    def test_check_endpoint_reachable_unreachable_host(self) -> None:
        """Probing an endpoint whose connect fails should return FAIL."""
        policy = NetworkPolicy(timeout_seconds=0.5)
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_raises(OSError("simulated: no route to host")):
            check = validator.check_endpoint_reachable(Endpoint("192.0.2.1", 1))
        assert check.status == CheckStatus.FAIL

    def test_check_endpoint_reachable_reachable_host(self) -> None:
        """Probing an endpoint whose connect succeeds should return PASS."""
        policy = NetworkPolicy(timeout_seconds=0.5)
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_succeeds():
            check = validator.check_endpoint_reachable(Endpoint("127.0.0.1", 8052))
        assert check.status == CheckStatus.PASS

    def test_blocked_endpoint_fails_when_reachable(self) -> None:
        """A denied endpoint that is reachable is a VIOLATION (FAIL)."""
        policy = NetworkPolicy(
            denied_endpoints=(Endpoint("192.0.2.1", 1),),
            timeout_seconds=0.5,
        )
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_succeeds():
            check = validator.check_endpoint_blocked(Endpoint("192.0.2.1", 1))
        assert check.status == CheckStatus.FAIL

    def test_validate_isolation_with_only_blocked(self) -> None:
        """Validation should pass when all denied endpoints are blocked."""
        policy = NetworkPolicy(
            denied_endpoints=(Endpoint("192.0.2.1", 1),),
            timeout_seconds=0.5,
            isolation_level=IsolationLevel.FULL,  # skip DNS check
        )
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_raises(OSError("simulated: connection refused")):
            result = validator.validate_isolation("agent-1")
        assert result.passed
        assert result.agent_id == "agent-1"

    def test_validate_isolation_empty_policy(self) -> None:
        """Empty policy with full isolation should pass (no checks to run)."""
        policy = NetworkPolicy(isolation_level=IsolationLevel.FULL)
        validator = NetworkIsolationValidator(policy)
        result = validator.validate_isolation("agent-1")
        assert result.passed

    def test_latency_recorded(self) -> None:
        policy = NetworkPolicy(timeout_seconds=0.5)
        validator = NetworkIsolationValidator(policy)
        with _socket_connect_raises(OSError("simulated: no route to host")):
            check = validator.check_endpoint_reachable(Endpoint("192.0.2.1", 1))
        assert check.latency_ms >= 0

    def test_policy_property(self) -> None:
        policy = NetworkPolicy()
        validator = NetworkIsolationValidator(policy)
        assert validator.policy == policy

    def test_dns_check_included_for_non_full_isolation(self) -> None:
        """DNS check should be included when isolation is not FULL."""
        policy = NetworkPolicy(
            isolation_level=IsolationLevel.RESTRICTED,
            dns_allowed=True,
        )
        validator = NetworkIsolationValidator(policy)
        with _dns_resolves(True):
            result = validator.validate_isolation("agent-1")
        dns_checks = [c for c in result.checks if c.name == "dns_resolution"]
        assert len(dns_checks) == 1

    def test_dns_check_skipped_for_full_isolation(self) -> None:
        """DNS check should be skipped when isolation is FULL."""
        policy = NetworkPolicy(isolation_level=IsolationLevel.FULL)
        validator = NetworkIsolationValidator(policy)
        result = validator.validate_isolation("agent-1")
        dns_checks = [c for c in result.checks if c.name == "dns_resolution"]
        assert len(dns_checks) == 0
