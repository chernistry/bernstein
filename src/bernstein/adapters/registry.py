"""Adapter registry - look up CLI adapters by name."""

from __future__ import annotations

import inspect
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from bernstein.adapters.aichat import AIChatAdapter
from bernstein.adapters.aider import AiderAdapter
from bernstein.adapters.amp import AmpAdapter
from bernstein.adapters.auggie import AuggieAdapter
from bernstein.adapters.autohand import AutohandAdapter
from bernstein.adapters.base import CLIAdapter
from bernstein.adapters.charm import CharmAdapter
from bernstein.adapters.claude import ClaudeCodeAdapter
from bernstein.adapters.cline import ClineAdapter
from bernstein.adapters.clm import ClmAdapter
from bernstein.adapters.cloudflare_agents import CloudflareAgentsAdapter
from bernstein.adapters.codebuff import CodebuffAdapter
from bernstein.adapters.codex import CodexAdapter
from bernstein.adapters.cody import CodyAdapter
from bernstein.adapters.composio import ComposioAdapter
from bernstein.adapters.continue_dev import ContinueDevAdapter
from bernstein.adapters.copilot import CopilotAdapter
from bernstein.adapters.cursor import CursorAdapter
from bernstein.adapters.devin_terminal import DevinTerminalAdapter
from bernstein.adapters.droid import DroidAdapter
from bernstein.adapters.forge import ForgeAdapter
from bernstein.adapters.gemini import GeminiAdapter
from bernstein.adapters.generic import GenericAdapter
from bernstein.adapters.goose import GooseAdapter
from bernstein.adapters.gptme import GptmeAdapter
from bernstein.adapters.hermes import HermesAdapter
from bernstein.adapters.iac import IaCAdapter
from bernstein.adapters.junie import JunieAdapter
from bernstein.adapters.kilo import KiloAdapter
from bernstein.adapters.kimi import KimiAdapter
from bernstein.adapters.kiro import KiroAdapter
from bernstein.adapters.letta_code import LettaCodeAdapter
from bernstein.adapters.mistral import MistralAdapter
from bernstein.adapters.mock import MockAgentAdapter
from bernstein.adapters.ollama import OllamaAdapter
from bernstein.adapters.open_interpreter import OpenInterpreterAdapter
from bernstein.adapters.openai_agents import OpenAIAgentsAdapter
from bernstein.adapters.opencode import OpenCodeAdapter
from bernstein.adapters.openhands import OpenHandsAdapter
from bernstein.adapters.pi import PiAdapter
from bernstein.adapters.plandex import PlandexAdapter
from bernstein.adapters.q_dev import QDevAdapter
from bernstein.adapters.qwen import QwenAdapter
from bernstein.adapters.ralphex import RalphexAdapter
from bernstein.adapters.rovo import RovoAdapter

logger = logging.getLogger(__name__)

_ADAPTERS: dict[str, type[CLIAdapter] | CLIAdapter] = {
    "aichat": AIChatAdapter,
    "aider": AiderAdapter,
    "amp": AmpAdapter,
    "auggie": AuggieAdapter,
    "autohand": AutohandAdapter,
    "charm": CharmAdapter,
    "claude": ClaudeCodeAdapter,
    "cline": ClineAdapter,
    "clm": ClmAdapter,
    "cloudflare": CloudflareAgentsAdapter,
    "codebuff": CodebuffAdapter,
    "codex": CodexAdapter,
    "cody": CodyAdapter,
    "composio": ComposioAdapter,
    "continue": ContinueDevAdapter,
    "copilot": CopilotAdapter,
    "cursor": CursorAdapter,
    "devin_terminal": DevinTerminalAdapter,
    "droid": DroidAdapter,
    "forge": ForgeAdapter,
    # The Google CLI ships under two binary names during the 2026-06-18
    # transition. Both registry keys resolve to the same dual-binary aware
    # adapter; the adapter discovers ``antigravity`` first on PATH and falls
    # back to ``gemini`` (or honours BERNSTEIN_GEMINI_BINARY) at spawn time.
    "antigravity": GeminiAdapter,
    "gemini": GeminiAdapter,
    "generic": GenericAdapter,
    "goose": GooseAdapter,
    "gptme": GptmeAdapter,
    "hermes": HermesAdapter,
    "iac": IaCAdapter,
    "junie": JunieAdapter,
    "kilo": KiloAdapter,
    "kimi": KimiAdapter,
    "kiro": KiroAdapter,
    "letta_code": LettaCodeAdapter,
    "mistral": MistralAdapter,
    "mock": MockAgentAdapter,
    "ollama": OllamaAdapter,
    "open_interpreter": OpenInterpreterAdapter,
    "openai_agents": OpenAIAgentsAdapter,
    "opencode": OpenCodeAdapter,
    "openhands": OpenHandsAdapter,
    "pi": PiAdapter,
    "plandex": PlandexAdapter,
    "q_dev": QDevAdapter,
    "qwen": QwenAdapter,
    "ralphex": RalphexAdapter,
    "rovo": RovoAdapter,
}

_entrypoints_loaded = False


def _load_entrypoint_adapters() -> None:
    """Discover and register adapters from the ``bernstein.adapters`` entry-point group.

    Called once on first use. Silently skips malformed plugins.
    """
    global _entrypoints_loaded
    if _entrypoints_loaded:
        return
    _entrypoints_loaded = True
    for ep in entry_points(group="bernstein.adapters"):
        try:
            loaded = ep.load()
            name = ep.name
            if (inspect.isclass(loaded) and issubclass(loaded, CLIAdapter)) or isinstance(loaded, CLIAdapter):
                _ADAPTERS[name] = loaded
            else:
                logger.warning(
                    "Ignoring entry-point adapter %r: expected CLIAdapter subclass or instance, got %r",
                    name,
                    loaded,
                )
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to load entry-point adapter %r: %s", ep.name, exc)


def get_adapter(cli_name: str) -> CLIAdapter:
    """Get adapter by name, e.g. 'aider', 'claude', 'cody', 'codex', 'continue', 'gemini', or 'generic'.

    For 'generic', returns a GenericAdapter with default settings.
    For known adapters, instantiates the corresponding class.
    Third-party adapters are discovered from the ``bernstein.adapters`` entry-point group.

    Args:
        cli_name: Adapter name to look up.

    Returns:
        An instantiated CLIAdapter.

    Raises:
        ValueError: If the adapter name is not recognized.
    """
    if cli_name == "generic":
        return GenericAdapter(cli_command="generic-cli", display_name="Generic CLI")

    _load_entrypoint_adapters()

    adapter_cls = _ADAPTERS.get(cli_name)
    if adapter_cls is None:
        available = ", ".join(sorted([*_ADAPTERS.keys(), "generic"]))
        raise ValueError(f"Unknown adapter '{cli_name}'. Available: {available}")

    if isinstance(adapter_cls, CLIAdapter):
        return adapter_cls
    return adapter_cls()


def registry_name_for(adapter: CLIAdapter) -> str | None:
    """Return the registry key an adapter instance is registered under.

    Resolves the canonical registry name (e.g. ``"claude"``) for a live
    adapter so callers can key per-adapter tables (such as the strategy
    matrix in :mod:`bernstein.adapters._contract`) without each adapter
    having to restate its own key.

    Resolution prefers an explicit :attr:`CLIAdapter.registry_name`, then
    falls back to a reverse lookup over the registered classes/instances.
    Entry-point discovery runs first so third-party adapters resolve too.

    Args:
        adapter: A live :class:`CLIAdapter` instance.

    Returns:
        The registry key, or ``None`` when the adapter is not registered.
    """
    explicit = getattr(adapter, "registry_name", "") or ""
    if explicit and explicit in _ADAPTERS:
        return explicit

    _load_entrypoint_adapters()
    adapter_type = type(adapter)
    for name, entry in _ADAPTERS.items():
        if entry is adapter:
            return name
        if inspect.isclass(entry) and entry is adapter_type:
            return name
    return None


def register_adapter(name: str, adapter: type[CLIAdapter] | CLIAdapter) -> None:
    """Register a custom adapter by name.

    Args:
        name: Name to register under.
        adapter: Adapter class or instance.
    """
    _ADAPTERS[name] = adapter


def iter_adapter_specs() -> Iterator[tuple[str, type[CLIAdapter] | CLIAdapter]]:
    """Yield every registered adapter as ``(name, class-or-instance)`` pairs.

    The iterator triggers entry-point discovery on first use so third-
    party adapters are surfaced alongside the built-ins. Pairs are
    emitted in alphabetic order by name so downstream consumers can
    rely on a deterministic enumeration.

    The values are the raw registry entries (either a class or a
    pre-built instance). Callers that need a live adapter should pass
    each one through :func:`get_adapter` or instantiate themselves.
    """
    _load_entrypoint_adapters()
    for name in sorted(_ADAPTERS.keys()):
        yield name, _ADAPTERS[name]
