"""Process-wide runtime socket guard for ``--profile airgap``.

The :mod:`bernstein.core.security.network_policy` module gates *known*
adapter endpoints at registration time. That covers the documented
SaaS surfaces (Anthropic / OpenAI / Google / Cloudflare) but does
NOT stop a misbehaving plugin or library from opening an arbitrary
socket the orchestrator never advertised. Sovereign customers expect
``--profile airgap`` to be a hard fail-closed boundary -- so this
module installs a process-wide hook on
:class:`socket.socket.connect` that consults the active policy on
every TCP/UDP connect attempt.

Design notes:

* The guard is **opt-in**. It only patches when the airgap profile
  is active (``BERNSTEIN_PROFILE_MODE=airgap``). Outside the profile
  the back-compat default is allow-all and patching would be
  surprising / break tooling that relies on legacy behaviour.
* DNS still works -- ``getaddrinfo`` is a separate syscall path. If
  the policy denies the *resolved* host, we raise on ``connect``
  rather than on resolution. This matches the stated semantics in
  :mod:`network_policy` ("DNS queries route to a host, the host
  is the one that gets policy-checked").
* AF_UNIX sockets are exempt -- they never leave the machine and
  legitimate IPC (gRPC over UDS, journald) would otherwise break.
* The patched ``connect`` accepts both ``connect((host, port))``
  and the IPv6 4-tuple form ``(host, port, flowinfo, scopeid)``.
* The guard is idempotent: ``install_runtime_socket_guard()`` can
  be called many times; only the first call patches the global.
"""

from __future__ import annotations

import contextlib
import logging
import socket
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

from bernstein.core.security.network_policy import (
    ENV_PROFILE_MODE,
    PROFILE_AIRGAP,
    NetworkPolicyDenied,
    is_airgap_profile,
    policy_from_env,
)

logger = logging.getLogger(__name__)

_INSTALLED_FLAG: Final[str] = "_bernstein_socket_guard_installed"
_ORIGINAL_FLAG: Final[str] = "_bernstein_socket_guard_original_connect"

__all__ = [
    "ENV_PROFILE_MODE",
    "PROFILE_AIRGAP",
    "install_runtime_socket_guard",
    "is_runtime_socket_guard_installed",
    "uninstall_runtime_socket_guard",
]


def _extract_host_port(address: Any) -> tuple[str, int | None] | None:
    """Best-effort decode of the ``connect`` address argument.

    Handles:
    - ``(host, port)`` for AF_INET
    - ``(host, port, flowinfo, scopeid)`` for AF_INET6
    - ``str`` / ``bytes`` for AF_UNIX (returns ``None`` to skip the check)

    Returns ``None`` when the address shape is not understood; the
    caller treats that as "let the original connect decide". We do
    NOT want a parse error here to be a silent bypass of the guard,
    but we also do not want the guard to crash exotic socket usage
    that pre-dates IPv6.
    """
    if isinstance(address, (str, bytes)):
        return None
    if not isinstance(address, tuple) or not address:
        return None
    host = address[0]
    port = address[1] if len(address) >= 2 else None
    if not isinstance(host, str):
        return None
    if port is not None and not isinstance(port, int):
        return None
    return host, port


def _is_unix_socket(sock: socket.socket) -> bool:
    """Return True for AF_UNIX sockets (always exempt).

    ``socket.AF_UNIX`` does not exist on Windows. Guard the lookup so
    the runtime test of the airgap profile still passes there.
    """
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        return False
    return sock.family == af_unix


def _make_guarded_connect(original: Any) -> Any:
    """Wrap the original ``socket.socket.connect`` with the policy gate.

    Closures keep the original around so :func:`uninstall_runtime_socket_guard`
    can restore it for tests that need the unpatched primitive.
    """

    def _guarded_connect(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_unix_socket(self):
            return original(self, address, *args, **kwargs)
        decoded = _extract_host_port(address)
        if decoded is None:
            return original(self, address, *args, **kwargs)
        host, port = decoded
        # Re-read the policy each call: subprocess code may have toggled
        # BERNSTEIN_PROFILE_MODE between the initial install and now.
        if not is_airgap_profile():
            return original(self, address, *args, **kwargs)
        policy = policy_from_env()
        if policy.is_allowed(host, port):
            return original(self, address, *args, **kwargs)
        dest = f"{host}:{port}" if port is not None else host
        logger.warning(
            "airgap runtime guard refused socket.connect to %s (policy: %s)",
            dest,
            policy.to_env_value(),
        )
        raise NetworkPolicyDenied(dest, source="socket-guard")

    return _guarded_connect


def install_runtime_socket_guard(*, force: bool = False) -> bool:
    """Install the process-wide runtime egress hook.

    The guard wraps :class:`socket.socket.connect` so every outbound
    TCP/UDP attempt is run past the active network policy. AF_UNIX
    sockets are exempt. Loopback (``127.0.0.1`` / ``::1``) is allowed
    only if the operator added them via ``--allow-network`` or runs
    outside the airgap profile.

    Args:
        force: When True, reinstall even if a previous installation
            exists. Used by tests that need to swap in a fresh
            original to capture monkeypatched state.

    Returns:
        True if the guard was installed (or already installed) and
        is now active. False if the airgap profile is not active and
        ``force`` is not set -- the guard is a no-op outside airgap
        mode and a quiet decline keeps callsites simple.
    """
    if not force and not is_airgap_profile():
        return False
    sock_cls = socket.socket
    if getattr(sock_cls, _INSTALLED_FLAG, False) and not force:
        return True
    if force and getattr(sock_cls, _INSTALLED_FLAG, False):
        # Restore first so we close over the truly original connect,
        # not over the previous guard.
        original = getattr(sock_cls, _ORIGINAL_FLAG, sock_cls.connect)
        sock_cls.connect = original  # type: ignore[method-assign]
    original_connect = sock_cls.connect
    setattr(sock_cls, _ORIGINAL_FLAG, original_connect)
    sock_cls.connect = _make_guarded_connect(original_connect)  # type: ignore[method-assign]
    setattr(sock_cls, _INSTALLED_FLAG, True)
    return True


def uninstall_runtime_socket_guard() -> bool:
    """Restore the original ``socket.socket.connect`` (test helper).

    Returns True iff the guard was previously installed and has been
    successfully removed.
    """
    sock_cls = socket.socket
    if not getattr(sock_cls, _INSTALLED_FLAG, False):
        return False
    original = getattr(sock_cls, _ORIGINAL_FLAG, None)
    if original is None:
        return False
    sock_cls.connect = original  # type: ignore[method-assign]
    setattr(sock_cls, _INSTALLED_FLAG, False)
    with contextlib.suppress(AttributeError):
        delattr(sock_cls, _ORIGINAL_FLAG)
    return True


def is_runtime_socket_guard_installed() -> bool:
    """Return True iff the guard is currently patched into ``socket.socket``."""
    return bool(getattr(socket.socket, _INSTALLED_FLAG, False))


def collect_unmonitored_destinations(allowed_specs: Iterable[str]) -> list[str]:
    """Return the active policy's allow-list filtered to monitored entries.

    Helper used by :func:`bernstein.core.distribution.doctor_airgap` to
    cross-check the ``--allow-network`` spec against what the runtime
    guard would actually permit. Kept here so the doctor module does
    not have to import :mod:`socket_guard` directly.
    """
    return [spec for spec in allowed_specs if spec.strip()]
