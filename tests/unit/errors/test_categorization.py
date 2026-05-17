"""Unit tests for ``bernstein.core.errors.categorization``.

Every category has at least two trigger-condition tests plus the
exit-code mapping test, so the full taxonomy stays exercised end to end.
"""

from __future__ import annotations

import errno
import subprocess

import pytest

from bernstein.core.errors import (
    AdapterAuthError,
    AdapterBinaryNotFoundError,
    BernsteinFirstRunError,
    ErrorCategory,
    categorize_exception,
    exit_code_for,
)

# ---------------------------------------------------------------------------
# CONFIG_MISSING
# ---------------------------------------------------------------------------


def test_config_missing_from_filename_attribute() -> None:
    exc = FileNotFoundError(errno.ENOENT, "No such file", "bernstein.yaml")
    assert categorize_exception(exc) is ErrorCategory.CONFIG_MISSING


def test_config_missing_from_nested_config_path() -> None:
    exc = FileNotFoundError(errno.ENOENT, "No such file", ".bernstein/config.yaml")
    assert categorize_exception(exc) is ErrorCategory.CONFIG_MISSING


def test_config_missing_from_oserror_with_config_filename() -> None:
    exc = OSError(errno.ENOENT, "no such file", "bernstein.yaml")
    assert categorize_exception(exc) is ErrorCategory.CONFIG_MISSING


def test_config_missing_from_typed_first_run_error() -> None:
    exc = BernsteinFirstRunError("no config", category=ErrorCategory.CONFIG_MISSING)
    assert categorize_exception(exc) is ErrorCategory.CONFIG_MISSING


# ---------------------------------------------------------------------------
# AUTH_FAILED
# ---------------------------------------------------------------------------


def test_auth_failed_from_adapter_marker() -> None:
    exc = AdapterAuthError("401 unauthorized", adapter="claude", env_var="ANTHROPIC_API_KEY")
    assert categorize_exception(exc) is ErrorCategory.AUTH_FAILED


def test_auth_failed_from_generic_runtime_with_auth_token() -> None:
    exc = RuntimeError("API key invalid: unauthorized")
    assert categorize_exception(exc) is ErrorCategory.AUTH_FAILED


def test_auth_failed_via_typed_first_run_error() -> None:
    exc = BernsteinFirstRunError("bad key", category=ErrorCategory.AUTH_FAILED)
    assert categorize_exception(exc) is ErrorCategory.AUTH_FAILED


# ---------------------------------------------------------------------------
# DEPENDENCY_MISSING
# ---------------------------------------------------------------------------


def test_dependency_missing_from_adapter_marker() -> None:
    exc = AdapterBinaryNotFoundError("claude not in PATH", adapter="claude")
    assert categorize_exception(exc) is ErrorCategory.DEPENDENCY_MISSING


def test_dependency_missing_via_typed_first_run_error() -> None:
    exc = BernsteinFirstRunError("binary missing", category=ErrorCategory.DEPENDENCY_MISSING)
    assert categorize_exception(exc) is ErrorCategory.DEPENDENCY_MISSING


# ---------------------------------------------------------------------------
# MODEL_UNREACHABLE
# ---------------------------------------------------------------------------


def test_model_unreachable_from_connection_error() -> None:
    exc = ConnectionError("connection refused")
    assert categorize_exception(exc) is ErrorCategory.MODEL_UNREACHABLE


def test_model_unreachable_from_connection_refused_subclass() -> None:
    exc = ConnectionRefusedError(errno.ECONNREFUSED, "refused")
    assert categorize_exception(exc) is ErrorCategory.MODEL_UNREACHABLE


def test_model_unreachable_from_oserror_with_econnrefused() -> None:
    exc = OSError(errno.ECONNREFUSED, "refused")
    assert categorize_exception(exc) is ErrorCategory.MODEL_UNREACHABLE


def test_model_unreachable_from_httpx_like_class_name() -> None:
    class ConnectError(Exception):
        pass

    exc = ConnectError("dns failure")
    assert categorize_exception(exc) is ErrorCategory.MODEL_UNREACHABLE


# ---------------------------------------------------------------------------
# TIMEOUT
# ---------------------------------------------------------------------------


def test_timeout_from_subprocess_timeout_expired() -> None:
    exc = subprocess.TimeoutExpired(cmd=["claude"], timeout=30)
    assert categorize_exception(exc) is ErrorCategory.TIMEOUT


def test_timeout_from_builtin_timeout_error() -> None:
    exc = TimeoutError("operation timed out")
    assert categorize_exception(exc) is ErrorCategory.TIMEOUT


def test_timeout_from_socket_timeout_alias() -> None:
    exc = TimeoutError("socket timed out")
    assert categorize_exception(exc) is ErrorCategory.TIMEOUT


def test_timeout_from_oserror_etimedout() -> None:
    exc = OSError(errno.ETIMEDOUT, "timed out")
    assert categorize_exception(exc) is ErrorCategory.TIMEOUT


def test_timeout_from_httpx_like_class_name() -> None:
    class ReadTimeout(Exception):
        pass

    exc = ReadTimeout("read timed out")
    assert categorize_exception(exc) is ErrorCategory.TIMEOUT


# ---------------------------------------------------------------------------
# PERMISSION_DENIED
# ---------------------------------------------------------------------------


def test_permission_denied_from_permission_error() -> None:
    exc = PermissionError(errno.EACCES, "denied", "/tmp/x")
    assert categorize_exception(exc) is ErrorCategory.PERMISSION_DENIED


def test_permission_denied_from_oserror_eacces() -> None:
    exc = OSError(errno.EACCES, "denied")
    assert categorize_exception(exc) is ErrorCategory.PERMISSION_DENIED


def test_permission_denied_from_oserror_eperm() -> None:
    exc = OSError(errno.EPERM, "not permitted")
    assert categorize_exception(exc) is ErrorCategory.PERMISSION_DENIED


# ---------------------------------------------------------------------------
# PORT_CONFLICT
# ---------------------------------------------------------------------------


def test_port_conflict_from_oserror_eaddrinuse() -> None:
    exc = OSError(errno.EADDRINUSE, "address already in use")
    assert categorize_exception(exc) is ErrorCategory.PORT_CONFLICT


def test_port_conflict_from_oserror_eaddrnotavail() -> None:
    exc = OSError(errno.EADDRNOTAVAIL, "cannot assign address")
    assert categorize_exception(exc) is ErrorCategory.PORT_CONFLICT


def test_port_conflict_from_generic_runtime_with_message() -> None:
    exc = RuntimeError("Port already in use on 8052")
    assert categorize_exception(exc) is ErrorCategory.PORT_CONFLICT


# ---------------------------------------------------------------------------
# UNKNOWN (fallback)
# ---------------------------------------------------------------------------


def test_unknown_for_bare_exception() -> None:
    assert categorize_exception(Exception("???")) is ErrorCategory.UNKNOWN


def test_unknown_for_value_error_without_known_tokens() -> None:
    assert categorize_exception(ValueError("bad input")) is ErrorCategory.UNKNOWN


def test_unknown_for_runtime_error_without_known_tokens() -> None:
    assert categorize_exception(RuntimeError("kaboom")) is ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Totality / robustness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        Exception(),
        BaseException(),
        ValueError("x"),
        RuntimeError(""),
        OSError(0, "no errno match"),
    ],
)
def test_categorize_never_returns_none_or_raises(exc: BaseException) -> None:
    category = categorize_exception(exc)
    assert isinstance(category, ErrorCategory)


def test_categorize_handles_bytes_filename() -> None:
    exc = FileNotFoundError(errno.ENOENT, "no", b"bernstein.yaml")
    assert categorize_exception(exc) is ErrorCategory.CONFIG_MISSING


def test_categorize_does_not_misfire_for_plain_filenotfound() -> None:
    exc = FileNotFoundError(errno.ENOENT, "missing", "data.bin")
    assert categorize_exception(exc) is ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Exit-code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "expected_code"),
    [
        (ErrorCategory.CONFIG_MISSING, 65),
        (ErrorCategory.AUTH_FAILED, 77),
        (ErrorCategory.DEPENDENCY_MISSING, 69),
        (ErrorCategory.MODEL_UNREACHABLE, 68),
        (ErrorCategory.TIMEOUT, 75),
        (ErrorCategory.PERMISSION_DENIED, 77),
        (ErrorCategory.PORT_CONFLICT, 75),
        (ErrorCategory.UNKNOWN, 70),
    ],
)
def test_exit_code_matches_sysexits_table(category: ErrorCategory, expected_code: int) -> None:
    assert exit_code_for(category) == expected_code


def test_exit_code_total_for_all_categories() -> None:
    for category in ErrorCategory:
        code = exit_code_for(category)
        assert 64 <= code <= 78, f"{category} -> {code} outside sysexits range"


# ---------------------------------------------------------------------------
# Typed first-run error round trip
# ---------------------------------------------------------------------------


def test_first_run_error_carries_context() -> None:
    exc = BernsteinFirstRunError(
        "missing key",
        category=ErrorCategory.AUTH_FAILED,
        context={"adapter": "claude", "env_var": "ANTHROPIC_API_KEY"},
    )
    assert exc.category is ErrorCategory.AUTH_FAILED
    assert exc.context["adapter"] == "claude"
    assert exc.context["env_var"] == "ANTHROPIC_API_KEY"


def test_first_run_error_repr_contains_category() -> None:
    exc = BernsteinFirstRunError("oops", category=ErrorCategory.PORT_CONFLICT)
    assert "port_conflict" in repr(exc)


def test_first_run_error_is_subclass_of_exception() -> None:
    exc = BernsteinFirstRunError("oops", category=ErrorCategory.UNKNOWN)
    assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# Adapter marker round trip
# ---------------------------------------------------------------------------


def test_adapter_auth_error_attributes_round_trip() -> None:
    exc = AdapterAuthError("nope", adapter="codex", env_var="OPENAI_API_KEY")
    assert exc.adapter == "codex"
    assert exc.env_var == "OPENAI_API_KEY"


def test_adapter_binary_not_found_attributes_round_trip() -> None:
    exc = AdapterBinaryNotFoundError("nope", adapter="aider", install_hint="pipx install aider")
    assert exc.adapter == "aider"
    assert exc.install_hint == "pipx install aider"
