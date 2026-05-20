"""Common base for task-subsystem domain exceptions.

Route handlers and middleware can write ``except TaskDomainError`` to
catch the entire family without enumerating each subclass.  Mirrors the
two-level hierarchy already used by ``core/credential_scoping.py``
(``CredentialScopingError`` and its subclasses).

The concrete subclasses (``EmptyCompletionError``,
``DuplicateTransitionError``, ``IllegalTransitionError``) live next to
the code that raises them.  Re-basing them on ``TaskDomainError`` does
not change their ``__str__`` output, so any callers that string-match
the message keep working unchanged.
"""

from __future__ import annotations


class TaskDomainError(Exception):
    """Base class for task-subsystem domain exceptions.

    Subclassed by ``EmptyCompletionError`` (task_store_core),
    ``DuplicateTransitionError`` and ``IllegalTransitionError``
    (lifecycle).  Route handlers can ``except TaskDomainError`` to
    intercept the whole family at once.
    """


__all__ = ["TaskDomainError"]
