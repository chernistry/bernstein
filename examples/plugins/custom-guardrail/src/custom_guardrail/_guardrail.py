"""Fail-closed input guardrail that rejects prompts containing secrets.

The implementation deliberately keeps state minimal — a list of compiled
regex patterns and the canonical name string. The plugin class wraps
the guardrail in a hookimpl that hands a fresh instance to the
orchestrator's :class:`GuardrailPipeline` on plugin load.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bernstein.core.security.guardrail_pipeline import (
    GuardrailPipeline,
    GuardrailResult,
)
from bernstein.plugins import hookimpl

# Canonical secret-shaped token regexes. Patterns are intentionally
# strict so a benign mention (``"my secret token"``) doesn't trip the
# guardrail; the goal is to catch *value-shaped* leaks, not vocabulary.
DEFAULT_PATTERNS: tuple[str, ...] = (
    # AWS — both the env-var name with a value and bare access-key IDs.
    r"AWS_SECRET_ACCESS_KEY\s*=\s*[A-Za-z0-9/+=]{16,}",
    r"AKIA[0-9A-Z]{16}",
    # GitHub — classic + fine-grained personal access tokens.
    r"ghp_[a-zA-Z0-9]{36}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    # OpenAI / generic ``sk-`` prefix tokens.
    r"sk-[a-zA-Z0-9]{20,}",
    # Slack tokens.
    r"xox[baprs]-[a-zA-Z0-9-]{10,}",
    # Bare ``GITHUB_TOKEN=...`` env-var leak.
    r"GITHUB_TOKEN\s*=\s*\S{20,}",
)


class NoSecretsGuardrail:
    """Block prompts containing canonical secret-shaped tokens.

    Works as a drop-in :class:`Guardrail` for the bernstein
    orchestrator's :class:`GuardrailPipeline`. Fail-closed: any match
    in any pattern aborts the run — there is no allow-list of
    "innocuous" matches.

    Attributes:
        name: Stable identifier surfaced in ``GuardrailResult``.
        extra_patterns: Operator-extensible regex list appended to
            :data:`DEFAULT_PATTERNS` on construction.
    """

    name: ClassVar[str] = "no_secrets_in_prompt"

    def __init__(self, *, extra_patterns: tuple[str, ...] = ()) -> None:
        self._compiled = [re.compile(p) for p in (*DEFAULT_PATTERNS, *extra_patterns)]

    def check_input(self, prompt: str, _context: dict[str, Any]) -> GuardrailResult:
        """Reject the prompt when any secret-shaped token is found."""
        violations: list[str] = []
        for pattern in self._compiled:
            if pattern.search(prompt):
                # Don't echo the matched value — it's a secret. Just
                # name the pattern so the operator can scrub the input.
                violations.append(f"Secret-shaped token matched pattern: {pattern.pattern}")
        return GuardrailResult(
            passed=len(violations) == 0,
            guardrail_name=self.name,
            violations=violations,
        )

    def check_output(self, _output: str, _context: dict[str, Any]) -> GuardrailResult:
        # Output side is the responsibility of the in-tree
        # ``SecretLeakGuardrail``; this plugin only owns the input side.
        return GuardrailResult(passed=True, guardrail_name=self.name)


class NoSecretsGuardrailPlugin:
    """Pluggy entry-point class wiring :class:`NoSecretsGuardrail` in.

    Bernstein discovers the plugin via the ``bernstein.plugins`` entry
    point group declared in ``pyproject.toml``. The plugin manager
    instantiates the class once per process and dispatches hooks.
    """

    @hookimpl
    def configure_guardrails(self, pipeline: GuardrailPipeline) -> None:
        """Append the no-secrets guardrail to *pipeline*.

        The hook is a thin extension point; if your bernstein build
        does not yet expose ``configure_guardrails`` the same plugin
        can register the guardrail by reaching into the orchestrator
        config — see ``README.md`` for the alternative wiring.
        """
        pipeline.add(NoSecretsGuardrail())

    def build_guardrail(self) -> NoSecretsGuardrail:
        """Return a fresh :class:`NoSecretsGuardrail` instance.

        Useful for callers that build the pipeline manually (tests,
        bespoke orchestrators) instead of going through the
        ``configure_guardrails`` hook.
        """
        return NoSecretsGuardrail()
