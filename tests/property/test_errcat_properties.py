"""Hypothesis property tests for the error categorization subsystem.

We assert structural invariants over arbitrary exceptions and context
shapes:

- ``categorize_exception`` is total (always returns an ``ErrorCategory``).
- ``exit_code_for`` always returns an integer inside the sysexits range.
- ``hint_for`` always returns a non-empty Rich panel.
- A typed :class:`BernsteinFirstRunError` round-trips its category.
- Arbitrary text contexts do not raise during hint rendering.
"""

from __future__ import annotations

import errno
import io

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rich.console import Console
from rich.panel import Panel

from bernstein.core.errors import (
    BernsteinFirstRunError,
    ErrorCategory,
    HintContext,
    categorize_exception,
    exit_code_for,
    hint_for,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_ERRNO_STRATEGY = st.sampled_from(
    [
        errno.ENOENT,
        errno.EACCES,
        errno.EPERM,
        errno.EADDRINUSE,
        errno.EADDRNOTAVAIL,
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
        errno.EIO,
    ]
)


def _build_exception(kind: int, message: str, errnum: int) -> BaseException:
    if kind == 0:
        return RuntimeError(message)
    if kind == 1:
        return ValueError(message)
    if kind == 2:
        return OSError(errnum, message or "os error")
    if kind == 3:
        return ConnectionError(message)
    if kind == 4:
        return PermissionError(errno.EACCES, message or "denied")
    if kind == 5:
        return FileNotFoundError(errno.ENOENT, message or "missing", "data.bin")
    if kind == 6:
        return TimeoutError(message or "timed out")
    if kind == 7:
        return Exception(message)
    # ``BaseException`` is the only path that exercises the bottom of the MRO.
    return BaseException(message)


@st.composite
def exceptions(draw: st.DrawFn) -> BaseException:
    kind = draw(st.integers(min_value=0, max_value=8))
    message = draw(st.text(max_size=64))
    errnum = draw(_ERRNO_STRATEGY)
    return _build_exception(kind, message, errnum)


_CATEGORIES = st.sampled_from(list(ErrorCategory))


@st.composite
def hint_contexts(draw: st.DrawFn) -> HintContext:
    ctx: HintContext = {}
    if draw(st.booleans()):
        ctx["adapter"] = draw(st.text(min_size=0, max_size=24))
    if draw(st.booleans()):
        ctx["env_var"] = draw(st.text(min_size=0, max_size=24))
    if draw(st.booleans()):
        ctx["provider"] = draw(st.text(min_size=0, max_size=24))
    if draw(st.booleans()):
        ctx["package_manager_command"] = draw(st.text(min_size=0, max_size=64))
    if draw(st.booleans()):
        ctx["timeout_seconds"] = draw(st.integers(min_value=0, max_value=86_400))
    if draw(st.booleans()):
        ctx["path"] = draw(st.text(min_size=0, max_size=64))
    if draw(st.booleans()):
        ctx["port"] = draw(st.integers(min_value=0, max_value=65_535))
    if draw(st.booleans()):
        ctx["repo"] = draw(st.text(min_size=0, max_size=32))
    return ctx


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@given(exc=exceptions())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_categorize_is_total(exc: BaseException) -> None:
    category = categorize_exception(exc)
    assert isinstance(category, ErrorCategory)


@given(category=_CATEGORIES)
def test_exit_code_is_in_sysexits_range(category: ErrorCategory) -> None:
    code = exit_code_for(category)
    assert isinstance(code, int)
    assert 64 <= code <= 78


@given(category=_CATEGORIES, ctx=hint_contexts())
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
def test_hint_for_returns_nonempty_panel(category: ErrorCategory, ctx: HintContext) -> None:
    panel = hint_for(category, ctx)
    assert isinstance(panel, Panel)
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, color_system=None, width=120).print(panel)
    rendered = buf.getvalue().strip()
    assert rendered != ""


@given(category=_CATEGORIES, message=st.text(max_size=64))
def test_first_run_error_roundtrips_category(category: ErrorCategory, message: str) -> None:
    exc = BernsteinFirstRunError(message or "x", category=category)
    assert categorize_exception(exc) is category


@given(category=_CATEGORIES)
def test_hint_for_handles_missing_context(category: ErrorCategory) -> None:
    panel = hint_for(category, None)
    assert isinstance(panel, Panel)


@given(exc=exceptions())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_categorize_then_exit_code_is_valid(exc: BaseException) -> None:
    category = categorize_exception(exc)
    code = exit_code_for(category)
    assert 64 <= code <= 78


@given(exc=exceptions())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_categorize_is_deterministic(exc: BaseException) -> None:
    # Calling twice on the same instance must yield the same category.
    assert categorize_exception(exc) is categorize_exception(exc)


@given(category=_CATEGORIES, ctx=hint_contexts())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_hint_panel_has_border_color(category: ErrorCategory, ctx: HintContext) -> None:
    panel = hint_for(category, ctx)
    # Every panel must declare a non-empty border style; this protects the
    # "category-coloured border" invariant from the spec.
    assert isinstance(panel.border_style, str)
    assert panel.border_style != ""


@given(port=st.integers(min_value=0, max_value=65_535))
def test_hint_port_conflict_echoes_port(port: int) -> None:
    panel = hint_for(ErrorCategory.PORT_CONFLICT, {"port": port})
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, color_system=None, width=120).print(panel)
    assert str(port) in buf.getvalue()


@given(seconds=st.integers(min_value=1, max_value=86_400))
def test_hint_timeout_echoes_seconds(seconds: int) -> None:
    panel = hint_for(ErrorCategory.TIMEOUT, {"adapter": "claude", "timeout_seconds": seconds})
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, color_system=None, width=120).print(panel)
    assert f"{seconds}s" in buf.getvalue()
