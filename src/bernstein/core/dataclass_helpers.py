"""Helpers for preserving dataclass instance types through updates."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar, Protocol, cast

__all__ = ["typed_replace"]


class _DataclassInstance(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]


def typed_replace[DataclassT: _DataclassInstance](instance: DataclassT, **changes: Any) -> DataclassT:
    """Return ``instance`` with ``changes`` while preserving the concrete type."""
    updated: object = replace(instance, **changes)
    return cast(DataclassT, updated)  # pyright: ignore[reportUnnecessaryCast]
