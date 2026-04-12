"""Backward-compat shim: module moved to bernstein.core.quality.cross_model_verifier."""

from bernstein.core.quality.cross_model_verifier import *  # noqa: F403
from bernstein.core.quality.cross_model_verifier import (  # noqa: F401
    _DEFAULT_REVIEWER,
    _MAX_DIFF_CHARS,
    _MAX_TOKENS,
    _PROVIDER,
    _REVIEW_PROMPT_TEMPLATE,
    _REVIEWER_CLAUDE_HAIKU,
    _REVIEWER_GEMINI_FLASH,
    _WRITER_TO_REVIEWER,
)
