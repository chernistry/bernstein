"""Registry-shape tests for ``bernstein.adapters.registry``.

Locks the public count narrative: ``bernstein adapters list`` and the
README must agree that 44 adapters ship today, with ``generic`` being one
of the 44 (registered in ``_ADAPTERS`` rather than special-cased only).
"""

from __future__ import annotations

from bernstein.adapters.generic import GenericAdapter
from bernstein.adapters.registry import _ADAPTERS, get_adapter


def test_generic_in_adapters_registry() -> None:
    """``generic`` must be a first-class entry in ``_ADAPTERS``.

    The ``bernstein adapters list`` command enumerates ``_ADAPTERS``; if
    ``generic`` is only served by the special-case branch in
    ``get_adapter``, it is invisible to the listing command and the
    README's adapter count drifts.
    """
    assert "generic" in _ADAPTERS


def test_adapter_count_at_least_44() -> None:
    """Lock the public adapter count cited in README / landing copy.

    Source of truth: ``len(_ADAPTERS)`` (also surfaced by
    ``bernstein adapters list``). If you add or remove an adapter, update
    README.md (lines for ``CLI agent adapters`` and the comparison tables)
    so the public count stays honest.
    """
    assert len(_ADAPTERS) >= 44, sorted(_ADAPTERS)


def test_get_adapter_generic_returns_generic_adapter() -> None:
    """``get_adapter('generic')`` must still resolve to a ``GenericAdapter``.

    Registry-dict registration must not break the existing special-case
    in ``get_adapter`` that returns a pre-configured GenericAdapter.
    """
    adapter = get_adapter("generic")
    assert isinstance(adapter, GenericAdapter)
