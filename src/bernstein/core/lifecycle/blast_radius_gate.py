"""Lifecycle wire-in for the blast-radius reversibility gate (issue #1322).

This module is the single integration point between the blast-radius scorer
in :mod:`bernstein.core.quality.blast_radius` and the merge / deploy gate.
It is intentionally tiny: orchestrator callers invoke
:func:`install_blast_radius_gate` after building a
:class:`bernstein.core.security.blocking_hooks.BlockingHookRunner`. The
function is a no-op unless the operator opted in via
``--max-blast-radius`` (which propagates the ``BERNSTEIN_MAX_BLAST_RADIUS``
env var).

Default behaviour stays unchanged: when the env var is unset, no hook is
registered and the gate is a pass-through.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bernstein.core.security.blocking_hooks import BlockingHookRunner

logger = logging.getLogger(__name__)

#: Operator-visible env var that carries the ceiling from `bernstein run`.
ENV_MAX_BLAST_RADIUS: str = "BERNSTEIN_MAX_BLAST_RADIUS"


def _read_ceiling() -> float | None:
    """Parse the env var and clamp to [0, 1]. Returns ``None`` when unset."""
    raw = os.environ.get(ENV_MAX_BLAST_RADIUS)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a valid float in [0, 1].", ENV_MAX_BLAST_RADIUS, raw)
        return None
    if not 0.0 <= value <= 1.0:
        logger.warning("Ignoring %s=%s: outside [0, 1].", ENV_MAX_BLAST_RADIUS, value)
        return None
    return value


def install_blast_radius_gate(runner: BlockingHookRunner) -> bool:
    """Register the blast-radius hook on ``runner`` if the env var is set.

    Args:
        runner: Existing :class:`BlockingHookRunner`.

    Returns:
        ``True`` when a hook was registered, ``False`` when the gate was
        skipped (env var unset or invalid).
    """
    ceiling = _read_ceiling()
    if ceiling is None:
        return False
    # Late import to avoid pulling YAML / scorer code into modules that
    # never opt in to the gate.
    from bernstein.core.quality.blast_radius import make_pre_merge_hook

    hook = make_pre_merge_hook(max_score=ceiling)
    runner.register("pre_merge", hook)
    logger.info("Blast-radius reversibility gate active: ceiling=%.4f (pre_merge).", ceiling)
    return True


__all__ = ["ENV_MAX_BLAST_RADIUS", "install_blast_radius_gate"]
