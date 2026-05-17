"""Map raw exceptions to a :class:`ErrorCategory`.

The categorizer is intentionally exhaustive: every ``BaseException`` is
mapped to exactly one category, with :attr:`ErrorCategory.UNKNOWN` as the
unconditional fallback.  This guarantees the CLI top-level handler can
always render a hint without needing further introspection.

The mapping is driven by exception type and a small number of attribute
probes (``errno``, ``filename``, message substring).  No I/O is performed.
"""

from __future__ import annotations

import errno
import socket
import subprocess
from typing import Final

from bernstein.core.errors.categories import (
    AdapterAuthError,
    AdapterBinaryNotFoundError,
    BernsteinFirstRunError,
    ErrorCategory,
)

_CONFIG_FILENAME_TOKENS: Final[tuple[str, ...]] = (
    "bernstein.yaml",
    "bernstein.toml",
    ".bernstein/config.yaml",
    ".bernstein/config.toml",
)

_PORT_ERRNOS: Final[frozenset[int]] = frozenset({errno.EADDRINUSE, errno.EADDRNOTAVAIL})

_MODEL_UNREACHABLE_ERRNOS: Final[frozenset[int]] = frozenset(
    {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNRESET}
)

_AUTH_TOKENS: Final[tuple[str, ...]] = (
    "auth",
    "unauthor",  # unauthorized / unauthorised
    "401",
    "403",
    "api key",
    "api_key",
    "credential",
    "permission denied by api",
)

_TIMEOUT_TOKENS: Final[tuple[str, ...]] = (
    "timed out",
    "timeout",
    "deadline exceeded",
)

_PORT_TOKENS: Final[tuple[str, ...]] = (
    "address already in use",
    "port already in use",
    "port is already in use",
    "bind: address",
)


def _filename_looks_like_config(filename: object) -> bool:
    """Return True if ``filename`` looks like a Bernstein config file."""
    if not isinstance(filename, (str, bytes)):
        return False
    text = filename.decode("utf-8", errors="ignore") if isinstance(filename, bytes) else filename
    return any(token in text for token in _CONFIG_FILENAME_TOKENS)


def _message_contains(exc: BaseException, tokens: tuple[str, ...]) -> bool:
    """Return True if any token is a case-insensitive substring of ``str(exc)``."""
    text = str(exc).casefold()
    return any(tok in text for tok in tokens)


def _categorize_oserror(exc: OSError) -> ErrorCategory | None:
    """Refine an ``OSError`` to a category, or ``None`` to fall through.

    Args:
        exc: The OSError to inspect.

    Returns:
        A category if one of the structured errno paths matches, else None.
    """
    err = exc.errno
    if err in _PORT_ERRNOS:
        return ErrorCategory.PORT_CONFLICT
    if err in _MODEL_UNREACHABLE_ERRNOS:
        return ErrorCategory.MODEL_UNREACHABLE
    if err == errno.ETIMEDOUT:
        return ErrorCategory.TIMEOUT
    if err == errno.EACCES or err == errno.EPERM:
        return ErrorCategory.PERMISSION_DENIED
    if err == errno.ENOENT and _filename_looks_like_config(getattr(exc, "filename", None)):
        return ErrorCategory.CONFIG_MISSING
    return None


def _categorize_by_class_name(exc: BaseException) -> ErrorCategory | None:
    """Best-effort categorisation when ``httpx`` / third-party types are missing.

    We avoid importing ``httpx`` directly so the package loads cleanly even
    when optional deps are absent. Instead, we walk ``type(exc).__mro__``
    and look at unqualified class names.

    Args:
        exc: The exception to inspect.

    Returns:
        A category, or ``None`` if no class-name match applies.
    """
    for cls in type(exc).__mro__:
        name = cls.__name__
        if name in {"ConnectError", "ConnectTimeout", "NetworkError", "RemoteProtocolError"}:
            return ErrorCategory.MODEL_UNREACHABLE
        if name in {"TimeoutException", "ReadTimeout", "WriteTimeout", "PoolTimeout"}:
            return ErrorCategory.TIMEOUT
    return None


def categorize_exception(exc: BaseException) -> ErrorCategory:
    """Map any ``BaseException`` to a single :class:`ErrorCategory`.

    The function is total: it never returns ``None`` and never raises.
    Unrecognised exceptions yield :attr:`ErrorCategory.UNKNOWN`.

    Args:
        exc: Any exception, typically caught at the CLI top level.

    Returns:
        The structured category for ``exc``.
    """
    # Typed first-run errors carry their category directly.
    if isinstance(exc, BernsteinFirstRunError):
        return exc.category

    # Adapter-domain markers.
    if isinstance(exc, AdapterAuthError):
        return ErrorCategory.AUTH_FAILED
    if isinstance(exc, AdapterBinaryNotFoundError):
        return ErrorCategory.DEPENDENCY_MISSING

    # Subprocess timeouts.
    if isinstance(exc, subprocess.TimeoutExpired):
        return ErrorCategory.TIMEOUT

    # Permission errors before generic OSError so the more-specific
    # PermissionError branch always wins.
    if isinstance(exc, PermissionError):
        return ErrorCategory.PERMISSION_DENIED

    # FileNotFoundError: only "config missing" when the filename hints at
    # a Bernstein config; otherwise fall through to UNKNOWN below.
    if isinstance(exc, FileNotFoundError):
        if _filename_looks_like_config(getattr(exc, "filename", None)):
            return ErrorCategory.CONFIG_MISSING
        if _message_contains(exc, _CONFIG_FILENAME_TOKENS):
            return ErrorCategory.CONFIG_MISSING
        # Treat any FileNotFoundError as a dependency-missing signal when
        # the message mentions an adapter binary path.  This stays
        # conservative: a bare missing data file becomes UNKNOWN.
        return ErrorCategory.UNKNOWN

    # ConnectionError covers httpx.ConnectError indirectly (the latter
    # subclasses httpx.NetworkError, not ConnectionError), but builtin
    # ConnectionRefusedError / ConnectionResetError / ConnectionAbortedError
    # all flow through this branch.
    if isinstance(exc, ConnectionError):
        return ErrorCategory.MODEL_UNREACHABLE

    # Socket-level timeouts (stdlib's socket.timeout aliases TimeoutError
    # on Python 3.10+; cover both for clarity).
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ErrorCategory.TIMEOUT

    # OSError errno-based refinement.
    if isinstance(exc, OSError):
        refined = _categorize_oserror(exc)
        if refined is not None:
            return refined

    # Third-party exception classes (httpx, requests, etc.) detected by
    # bare class name to avoid importing optional deps here.
    by_name = _categorize_by_class_name(exc)
    if by_name is not None:
        return by_name

    # Last-resort message sniffing for adapters that raise generic
    # RuntimeError/Exception. Order matters: auth before port before
    # timeout to keep the most specific cause first.
    if _message_contains(exc, _AUTH_TOKENS):
        return ErrorCategory.AUTH_FAILED
    if _message_contains(exc, _PORT_TOKENS):
        return ErrorCategory.PORT_CONFLICT
    if _message_contains(exc, _TIMEOUT_TOKENS):
        return ErrorCategory.TIMEOUT

    return ErrorCategory.UNKNOWN
