"""custom-guardrail — fail-closed no-secrets-in-prompt guardrail.

Worked example of the bernstein guardrail extension point. The plugin
contributes a :class:`Guardrail` implementation that scans incoming
prompts for canonical secret-shaped tokens and rejects the run when any
pattern matches.

The plugin registers itself via the ``bernstein.plugins`` entry-point
group; bernstein's plugin manager picks it up at startup and the
guardrail is appended to the default pipeline.
"""

from __future__ import annotations

from custom_guardrail._guardrail import (
    DEFAULT_PATTERNS,
    NoSecretsGuardrail,
    NoSecretsGuardrailPlugin,
)

__all__ = [
    "DEFAULT_PATTERNS",
    "NoSecretsGuardrail",
    "NoSecretsGuardrailPlugin",
]
