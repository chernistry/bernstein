"""Backward-compat shim: module moved to bernstein.core.quality.quality_gates."""

from bernstein.core.quality.quality_gates import *  # noqa: F403
from bernstein.core.quality.quality_gates import (  # noqa: F401
    _FORK_CONTEXT_MAX_CHARS,
    _INTENT_DEFAULT_MODEL,
    _INTENT_MAX_DIFF_CHARS,
    _INTENT_MAX_TOKENS,
    _INTENT_PROMPT_TEMPLATE,
    _INTENT_PROVIDER,
    _SOURCE_FROM_TEST,
    _TEST_FILE_PATTERN,
)
