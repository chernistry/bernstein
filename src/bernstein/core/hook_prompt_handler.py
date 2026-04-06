"""HOOK-006: Prompt handler type for hook events.

Injects additional context into the agent prompt when a hook event fires.
Prompt handlers return a string snippet that is appended to the agent's
system prompt or task description.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bernstein.core.hook_events import HookEvent, HookPayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptInjection:
    """A context snippet to inject into an agent prompt.

    Attributes:
        source: Name of the hook that produced this injection.
        content: The text to inject into the prompt.
        position: Where to inject (``"prepend"`` or ``"append"``).
        metadata: Optional metadata about the injection.
    """

    source: str
    content: str
    position: str = "append"
    metadata: dict[str, Any] = field(default_factory=dict)


class PromptHookHandler:
    """Async-callable that generates prompt context from hook events.

    Uses a template string with ``{event}``, ``{payload.*}`` placeholders
    that are expanded from the hook payload.

    Args:
        name: Handler name for identification.
        template: Template string with placeholders.
        position: Injection position (``"prepend"`` or ``"append"``).
    """

    def __init__(
        self,
        name: str,
        template: str,
        position: str = "append",
    ) -> None:
        self.name = name
        self.template = template
        self.position = position
        self._injections: list[PromptInjection] = []

    @property
    def injections(self) -> list[PromptInjection]:
        """Return all injections produced by this handler."""
        return list(self._injections)

    def _render_template(self, event: HookEvent, payload: HookPayload) -> str:
        """Render the template with payload values.

        Placeholders use ``{key}`` syntax.  Available keys:
        - ``event``: The event value string (e.g. ``"task.failed"``).
        - Any top-level key from ``payload.to_dict()``.

        Unknown placeholders are left as-is.

        Args:
            event: The hook event.
            payload: The hook payload.

        Returns:
            The rendered template string.
        """
        payload_dict = payload.to_dict()
        substitutions: dict[str, str] = {"event": event.value}
        for key, value in payload_dict.items():
            if isinstance(value, (str, int, float, bool)):
                substitutions[key] = str(value)

        result = self.template
        for key, value in substitutions.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    async def __call__(self, event: HookEvent, payload: HookPayload) -> None:
        """Generate a prompt injection from the event.

        The injection is stored internally and can be retrieved via
        :attr:`injections`.

        Args:
            event: The hook event.
            payload: The hook payload.
        """
        content = self._render_template(event, payload)
        injection = PromptInjection(
            source=self.name,
            content=content,
            position=self.position,
        )
        self._injections.append(injection)
        logger.debug(
            "Prompt injection from %r for %s: %s",
            self.name,
            event.value,
            content[:100],
        )

    def clear(self) -> None:
        """Clear accumulated injections."""
        self._injections.clear()


class PromptAggregator:
    """Collects prompt injections from multiple handlers and builds a final prompt.

    Usage::

        agg = PromptAggregator()
        agg.add(injection1)
        agg.add(injection2)
        final = agg.build("Original prompt")
    """

    def __init__(self) -> None:
        self._injections: list[PromptInjection] = []

    def add(self, injection: PromptInjection) -> None:
        """Add a prompt injection.

        Args:
            injection: The injection to add.
        """
        self._injections.append(injection)

    def add_all(self, injections: list[PromptInjection]) -> None:
        """Add multiple prompt injections.

        Args:
            injections: The injections to add.
        """
        self._injections.extend(injections)

    @property
    def injections(self) -> list[PromptInjection]:
        """Return all accumulated injections."""
        return list(self._injections)

    def build(self, base_prompt: str) -> str:
        """Build the final prompt with all injections applied.

        ``"prepend"`` injections are placed before the base prompt,
        ``"append"`` injections after.

        Args:
            base_prompt: The original prompt text.

        Returns:
            The prompt with all injections applied.
        """
        prepend_parts: list[str] = []
        append_parts: list[str] = []
        for inj in self._injections:
            if inj.position == "prepend":
                prepend_parts.append(inj.content)
            else:
                append_parts.append(inj.content)
        parts = [*prepend_parts, base_prompt, *append_parts]
        return "\n".join(parts)

    def clear(self) -> None:
        """Clear all accumulated injections."""
        self._injections.clear()
