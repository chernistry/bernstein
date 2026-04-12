"""Backward-compat shim: module moved to bernstein.core.quality.review_rubric."""

from bernstein.core.quality.review_rubric import *  # noqa: F403
from bernstein.core.quality.review_rubric import (  # noqa: F401
    _DEFAULT_MODEL,
    _DEFAULT_PROVIDER,
    _DIMENSION_WEIGHTS,
    _MAX_DIFF_CHARS,
    _MAX_TOKENS,
    _PROMPT_TEMPLATE,
)
