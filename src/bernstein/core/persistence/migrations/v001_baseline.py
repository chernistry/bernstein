"""Baseline migration: encodes the current on-disk shape.

This is the first migration in the package. Its ``apply`` is intentionally a
no-op beyond establishing the ``.sdd`` directory: it marks the point at which
the install adopted versioned migrations. Every shape the codebase produced
before this migration existed is, by definition, "version 1".

Subsequent shape changes ship as ``v002_*``, ``v003_*`` and so on, each with
a real ``apply`` that transforms the on-disk state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

VERSION = 1
DESCRIPTION = "baseline: adopt versioned migrations"


def apply(state_dir: Path) -> None:
    """Establish the baseline.

    No-op transform: the existing on-disk shape is already "version 1". We
    only ensure the state directory exists so the stamp has somewhere to
    land. Idempotent: creating a directory that exists is a no-op.
    """
    state_dir.mkdir(parents=True, exist_ok=True)


def down(state_dir: Path) -> None:
    """Roll back the baseline.

    Forward-only: there is nothing meaningful to undo for the baseline, so
    this is a stub. We deliberately do not delete the state directory.
    """
