"""LLM watcher: opt-in advisory observer above the deterministic orchestrator.

The watcher is the **top** of Bernstein's three-layer architecture
("deterministic orchestrator below, immutable HMAC chain in the middle,
LLM observer above" — see ticket
``2026-05-07-feat-llm-watcher-haiku-observer.md``).

Read-only contract
------------------
The watcher is structurally read-only:

1. The public ``observe`` API only accepts an immutable, frozen
   ``WatcherEvent`` snapshot.  It receives **no** orchestrator handle,
   no task store, no agent spawner, no filesystem path.  There is no
   capability inside this module to mutate orchestrator state — the
   omission is the enforcement.
2. The return type is ``list[Suggestion]`` — pure advisory data.
   Suggestions are advisory.  The orchestrator decides whether to log,
   surface, or persist them.  The watcher itself never writes to
   ``.sdd/backlog/``, ``.sdd/runtime/state/``, or any source file.
3. Failures inside the watcher (exceptions from the LLM adapter,
   timeout, network) are caught and converted into an empty signal
   list.  A misbehaving watcher cannot crash the orchestrator.

Off-by-default
--------------
The watcher is disabled by default.  The orchestrator emits zero
events and makes zero LLM calls unless ``WatcherConfig.enabled`` is
explicitly set to ``True`` — for example via
``BERNSTEIN_LLM_WATCHER_ENABLED=1`` or a future
``.sdd/config/watcher.yaml``.

This is the first plumbing slice for the P1 ticket.  Detector packs
(``stuck_loop``, ``plan_drift``, ``budget_overrun``,
``failure_recurrence``, ``jailbreak_shape``), the suggestion-review
CLI, and cost guardrails are deferred to follow-up tickets.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "EventKind",
    "LLMWatcher",
    "Severity",
    "Suggestion",
    "WatcherConfig",
    "WatcherEvent",
    "build_watcher_from_env",
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

#: Recognised orchestrator event kinds the watcher subscribes to.
EventKind = Literal[
    "plan_decided",
    "task_spawned",
    "task_completed",
    "merge_decided",
]

#: Advisory severity levels emitted by the watcher.
Severity = Literal["info", "warning", "critical"]

# Default Anthropic Haiku alias resolved by the existing Claude adapter
# (see ``bernstein.adapters.claude._MODEL_MAP``).  Kept as a string so
# the watcher never imports the adapter at module import time.
_DEFAULT_MODEL: Final[str] = "haiku"
_DEFAULT_PROVIDER: Final[str] = "claude"

_ENABLED_ENV_VAR: Final[str] = "BERNSTEIN_LLM_WATCHER_ENABLED"
_MODEL_ENV_VAR: Final[str] = "BERNSTEIN_LLM_WATCHER_MODEL"
_PROVIDER_ENV_VAR: Final[str] = "BERNSTEIN_LLM_WATCHER_PROVIDER"
_TRUTHY_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class WatcherEvent:
    """Immutable snapshot of an orchestrator event.

    The frozen dataclass is the **only** input surface to ``observe``.
    It carries no callable references, no mutable references to
    orchestrator state, no task-store handles.  The watcher therefore
    cannot mutate orchestrator state by construction.

    Attributes:
        kind: One of the recognised :data:`EventKind` values.
        run_id: Identifier of the orchestrator run that produced this
            event.
        timestamp: Unix epoch (seconds) when the event was created.
        payload: Free-form sanitised JSON-serialisable payload describing
            the event (e.g., plan summary, task id, merge decision).
            Callers MUST NOT include callable objects, file handles, or
            references to orchestrator-internal mutable state.
    """

    kind: EventKind
    run_id: str
    timestamp: float
    payload: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Advisory signal produced by the watcher.

    Suggestions are **never** auto-applied.  The orchestrator (or a
    human via a future CLI) decides whether to act on them.

    Attributes:
        suggestion_id: Stable identifier for cross-referencing in logs.
        run_id: Run that the originating event belonged to.
        detector: Free-form detector name (``stuck_loop``,
            ``plan_drift``, …).  In this first slice the watcher emits
            a generic ``observer`` detector; the detector pack lands in
            a follow-up ticket.
        severity: Advisory severity (``info`` | ``warning`` |
            ``critical``).
        rationale: Short human-readable explanation.
        proposed_action: Suggested next step (informational only —
            never executed automatically).
        cost_usd: Estimated USD cost of the LLM call that produced this
            suggestion.  ``0.0`` when the watcher short-circuits.
    """

    suggestion_id: str
    run_id: str
    detector: str
    severity: Severity
    rationale: str
    proposed_action: str
    cost_usd: float


@dataclass(frozen=True, slots=True)
class WatcherConfig:
    """Configuration for :class:`LLMWatcher`.

    Off by default.  The orchestrator constructs this from environment
    variables / future ``.sdd/config/watcher.yaml`` and passes it in.

    Attributes:
        enabled: Master switch.  When ``False`` (default) the watcher
            short-circuits ``observe`` to an empty list and makes zero
            LLM calls.
        model: Short model alias understood by the existing Claude
            adapter (default: ``"haiku"``).
        provider: Provider name routed through
            :func:`bernstein.core.llm.call_llm` (default: ``"claude"``).
        max_response_tokens: Cap on watcher LLM response length to
            keep the cost ceiling predictable.
        timeout_seconds: Hard timeout per LLM call; on expiry the
            watcher returns no suggestions for the event.
    """

    enabled: bool = False
    model: str = _DEFAULT_MODEL
    provider: str = _DEFAULT_PROVIDER
    max_response_tokens: int = 256
    timeout_seconds: float = 5.0


# Type for the LLM caller injected into :class:`LLMWatcher`.  Matches
# the signature of :func:`bernstein.core.routing.llm.call_llm`.  Kept
# as a Callable so tests can inject a stub without monkey-patching the
# real LLM module.
LLMCaller = Callable[..., Awaitable[str]]


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class LLMWatcher:
    """Opt-in LLM observer that emits advisory signals.

    Read-only contract
    ------------------
    By design this class:

    * accepts only :class:`WatcherEvent` snapshots through ``observe``;
    * receives no orchestrator/task-store handle in its constructor;
    * never imports task / agent / filesystem-mutation modules;
    * catches every exception from the underlying LLM caller and
      degrades to an empty signal list — the orchestrator is never
      crashed by watcher failure.

    Disabled-by-default
    -------------------
    When ``config.enabled`` is ``False`` the watcher short-circuits
    immediately and performs zero LLM calls.
    """

    def __init__(
        self,
        config: WatcherConfig,
        llm_caller: LLMCaller | None = None,
    ) -> None:
        """Initialise the watcher.

        Args:
            config: Watcher configuration; must be off by default.
            llm_caller: Optional injection seam for unit tests.  When
                ``None`` the watcher lazily imports
                :func:`bernstein.core.llm.call_llm` on first use, so a
                disabled watcher never imports the LLM stack.
        """
        self._config = config
        self._llm_caller = llm_caller
        self._call_count = 0
        self._suggestion_count = 0

    @property
    def config(self) -> WatcherConfig:
        """Return the watcher configuration (read-only)."""
        return self._config

    @property
    def call_count(self) -> int:
        """Number of LLM calls the watcher has issued."""
        return self._call_count

    @property
    def suggestion_count(self) -> int:
        """Number of suggestions the watcher has produced."""
        return self._suggestion_count

    async def observe(self, event: WatcherEvent) -> list[Suggestion]:
        """Process an orchestrator event and return advisory signals.

        Args:
            event: Immutable snapshot of an orchestrator event.

        Returns:
            A list of :class:`Suggestion` records.  Empty when the
            watcher is disabled, when the LLM call fails, when it
            times out, or when the model produces no advisory signal.
            **Never raises** to the caller — orchestrator stability is
            non-negotiable.
        """
        if not self._config.enabled:
            return []

        try:
            response = await self._invoke_llm(event)
        except Exception:
            # Orchestrator stability is non-negotiable.  A misbehaving
            # watcher MUST NOT crash the loop.
            logger.warning(
                "LLM watcher failed for event=%s run=%s; degrading to no signals",
                event.kind,
                event.run_id,
                exc_info=True,
            )
            return []

        if not response.strip():
            return []

        suggestion = self._build_suggestion(event, response)
        self._suggestion_count += 1
        return [suggestion]

    async def _invoke_llm(self, event: WatcherEvent) -> str:
        """Call the watcher LLM with a minimal advisory prompt.

        Args:
            event: Event to observe.

        Returns:
            Raw response text.
        """
        caller = self._llm_caller or _default_llm_caller()
        prompt = self._render_prompt(event)
        self._call_count += 1
        return await caller(
            prompt,
            self._config.model,
            provider=self._config.provider,
            max_tokens=self._config.max_response_tokens,
            temperature=0.0,
        )

    def _render_prompt(self, event: WatcherEvent) -> str:
        """Render the advisory prompt for a single event.

        The prompt deliberately frames the watcher as **read-only** and
        forbids the LLM from proposing mutations.  Detector-specific
        prompts land in a follow-up ticket.

        Args:
            event: Event to observe.

        Returns:
            Prompt text fed to the watcher LLM.
        """
        return (
            "You are an advisory observer of a deterministic agent orchestrator.\n"
            "You have READ-ONLY access. You CANNOT spawn agents, edit files, or modify state.\n"
            "Your only output is a single advisory line (or empty if nothing is unusual).\n\n"
            f"Event kind: {event.kind}\n"
            f"Run id: {event.run_id}\n"
            f"Payload: {event.payload}\n\n"
            "If you observe an anomaly, drift, or missed opportunity, "
            "respond with one short sentence describing it. "
            "Otherwise, respond with nothing."
        )

    def _build_suggestion(
        self,
        event: WatcherEvent,
        response: str,
    ) -> Suggestion:
        """Convert a raw LLM response into a structured suggestion.

        Args:
            event: Event the response is about.
            response: Raw text from the watcher LLM.

        Returns:
            A frozen :class:`Suggestion`.
        """
        rationale = response.strip().splitlines()[0][:512]
        suggestion_id = (
            f"watch-{event.run_id}-{event.kind}-{int(event.timestamp * 1000)}"
        )
        return Suggestion(
            suggestion_id=suggestion_id,
            run_id=event.run_id,
            detector="observer",
            severity="info",
            rationale=rationale,
            proposed_action="Review the orchestrator log for this event.",
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def _is_truthy(value: str | None) -> bool:
    """Return True when *value* matches a known truthy token."""
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_VALUES


def build_watcher_from_env(
    *,
    llm_caller: LLMCaller | None = None,
) -> LLMWatcher:
    """Build a watcher from environment variables.

    Recognised variables
    --------------------
    ``BERNSTEIN_LLM_WATCHER_ENABLED``
        Master switch (``1`` / ``true`` / ``yes`` / ``on`` to enable).
        Anything else, including unset, leaves the watcher off.
    ``BERNSTEIN_LLM_WATCHER_MODEL``
        Model alias (default: ``haiku``).
    ``BERNSTEIN_LLM_WATCHER_PROVIDER``
        Provider name (default: ``claude``).

    Args:
        llm_caller: Optional injection seam (mainly for tests).

    Returns:
        A configured but disabled-by-default :class:`LLMWatcher`.
    """
    enabled = _is_truthy(os.environ.get(_ENABLED_ENV_VAR))
    model = os.environ.get(_MODEL_ENV_VAR, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    provider = (
        os.environ.get(_PROVIDER_ENV_VAR, _DEFAULT_PROVIDER).strip()
        or _DEFAULT_PROVIDER
    )
    config = WatcherConfig(enabled=enabled, model=model, provider=provider)
    return LLMWatcher(config=config, llm_caller=llm_caller)


def _default_llm_caller() -> LLMCaller:
    """Lazy import of the project-wide LLM caller.

    Importing :func:`bernstein.core.llm.call_llm` at module top-level
    would pull in pydantic-settings, the OpenAI client, and the rest
    of the routing stack on every Bernstein boot — even when the
    watcher is disabled.  Deferring the import keeps the
    off-by-default path free of side effects.

    The ``bernstein.core.llm`` name is resolved at runtime via the
    ``_REDIRECT_MAP`` finder registered on :data:`sys.meta_path` (see
    :mod:`bernstein.core.__init__`).  Existing call sites
    (e.g. ``bernstein.core.quality.janitor``) import the same way.

    Returns:
        The project-wide async LLM caller.
    """
    from bernstein.core.llm import call_llm

    return call_llm
