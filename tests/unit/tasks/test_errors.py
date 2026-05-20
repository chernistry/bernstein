"""Tests for the ``TaskDomainError`` hierarchy.

The base class is plain ``Exception``; the three concrete task-subsystem
exceptions inherit from it so route handlers can catch them with a
single ``except TaskDomainError`` clause.  This test pins the
inheritance so a future direct-on-``Exception`` re-introduction fails
fast.
"""

from __future__ import annotations

from bernstein.core.tasks.errors import TaskDomainError
from bernstein.core.tasks.lifecycle import DuplicateTransitionError, IllegalTransitionError
from bernstein.core.tasks.task_store_core import EmptyCompletionError


def test_empty_completion_error_subclasses_task_domain_error() -> None:
    """``EmptyCompletionError`` must inherit from ``TaskDomainError``."""
    assert issubclass(EmptyCompletionError, TaskDomainError)


def test_duplicate_transition_error_subclasses_task_domain_error() -> None:
    """``DuplicateTransitionError`` must inherit from ``TaskDomainError``."""
    assert issubclass(DuplicateTransitionError, TaskDomainError)


def test_illegal_transition_error_subclasses_task_domain_error() -> None:
    """``IllegalTransitionError`` must inherit from ``TaskDomainError``."""
    assert issubclass(IllegalTransitionError, TaskDomainError)


def test_task_domain_error_subclasses_exception() -> None:
    """The base remains a plain ``Exception`` subclass."""
    assert issubclass(TaskDomainError, Exception)


def test_empty_completion_error_str_unchanged() -> None:
    """Re-basing must not change ``__str__`` (callers may string-match)."""
    err = EmptyCompletionError("task-123")
    message = str(err)
    assert "task-123" in message
    assert "result_summary must be non-empty" in message
    assert "completion missing summary" in message


def test_duplicate_transition_error_str_unchanged() -> None:
    """Re-basing must not change ``__str__``."""
    err = DuplicateTransitionError("tx-abc")
    assert str(err) == "Duplicate transition_id: 'tx-abc'"


def test_illegal_transition_error_str_unchanged() -> None:
    """Re-basing must not change ``__str__``."""
    err = IllegalTransitionError("task", "T-9", "PLANNED", "DONE")
    assert str(err) == "Illegal task transition: 'PLANNED' -> 'DONE' (entity T-9)"


def test_catch_family_with_single_except_clause() -> None:
    """All three errors are caught by a single ``except TaskDomainError``."""
    raised: list[type[BaseException]] = []
    for exc in (
        EmptyCompletionError("t1"),
        DuplicateTransitionError("tx"),
        IllegalTransitionError("task", "T-1", "A", "B"),
    ):
        try:
            raise exc
        except TaskDomainError as caught:
            raised.append(type(caught))
    assert raised == [EmptyCompletionError, DuplicateTransitionError, IllegalTransitionError]
