"""Per-adapter perspective assignment and chain coordination for reviews.

This package introduces a smaller-scope, schema-light counterpart to the
existing :mod:`bernstein.core.quality.review_pipeline` DSL. It assigns each
reviewer adapter a named *perspective* (``security``, ``performance`` …)
and runs them either in parallel or as a sequential chain where each
adapter receives the prior adapters' verdicts as additional context.

Public API:

* :class:`PerspectiveSpec`, :class:`PerspectiveConfig`
* :class:`PerspectiveVerdict`
* :class:`PerspectiveAdapterCall`
* :func:`load_perspectives_yaml`, :func:`load_perspectives`
* :func:`run_perspectives`

See :mod:`bernstein.core.review.perspectives` for details.
"""

from __future__ import annotations

from bernstein.core.review.perspectives import (
    ChainMode,
    PerspectiveAdapterCall,
    PerspectiveConfig,
    PerspectiveConfigError,
    PerspectiveSpec,
    PerspectiveVerdict,
    load_perspectives,
    load_perspectives_yaml,
    run_perspectives,
)

__all__ = [
    "ChainMode",
    "PerspectiveAdapterCall",
    "PerspectiveConfig",
    "PerspectiveConfigError",
    "PerspectiveSpec",
    "PerspectiveVerdict",
    "load_perspectives",
    "load_perspectives_yaml",
    "run_perspectives",
]
