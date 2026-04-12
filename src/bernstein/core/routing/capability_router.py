"""Capability-based addressing for agents — find by skill, not by name.

Instead of assigning tasks to specific adapters/models, tasks specify
required capabilities: ``requires: [python, testing, refactoring]``.
The router matches capabilities to available agents, decoupling task
definitions from specific providers.

Includes two layers:
1.  **CapabilityRouter** — lightweight matcher that works against
    ``DiscoveryResult`` objects obtained from agent_discovery probes.
2.  **CapabilityRegistry** — explicit registration of agents with typed
    ``Capability`` descriptors (name + level + description).  This enables
    fine-grained skill-level matching (basic / advanced / expert) and
    produces ranked ``RegistryMatch`` results.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bernstein.core.agents.agent_discovery import AgentCapabilities, DiscoveryResult

logger = logging.getLogger(__name__)


# ===================================================================
# Capability-level types (issue #647)
# ===================================================================


class CapabilityLevel(Enum):
    """Proficiency level for a single capability."""

    BASIC = "basic"
    ADVANCED = "advanced"
    EXPERT = "expert"

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, CapabilityLevel):
            return NotImplemented
        order = {CapabilityLevel.BASIC: 0, CapabilityLevel.ADVANCED: 1, CapabilityLevel.EXPERT: 2}
        return order[self] >= order[other]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, CapabilityLevel):
            return NotImplemented
        order = {CapabilityLevel.BASIC: 0, CapabilityLevel.ADVANCED: 1, CapabilityLevel.EXPERT: 2}
        return order[self] > order[other]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CapabilityLevel):
            return NotImplemented
        order = {CapabilityLevel.BASIC: 0, CapabilityLevel.ADVANCED: 1, CapabilityLevel.EXPERT: 2}
        return order[self] <= order[other]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CapabilityLevel):
            return NotImplemented
        order = {CapabilityLevel.BASIC: 0, CapabilityLevel.ADVANCED: 1, CapabilityLevel.EXPERT: 2}
        return order[self] < order[other]


@dataclass(frozen=True)
class Capability:
    """A single typed capability with proficiency level.

    Attributes:
        name: Canonical capability name (e.g. ``"python"``).
        level: Proficiency level (basic, advanced, expert).
        description: Optional human-readable description of what this entails.
    """

    name: str
    level: CapabilityLevel = CapabilityLevel.BASIC
    description: str = ""


@dataclass(frozen=True)
class AgentProfile:
    """Snapshot of an agent's capabilities for registry-based routing.

    Attributes:
        adapter_name: Adapter identifier (e.g. ``"claude"``, ``"codex"``).
        model: Model identifier (e.g. ``"claude-opus-4-0520"``).
        capabilities: Immutable set of capabilities this agent provides.
    """

    adapter_name: str
    model: str
    capabilities: frozenset[Capability] = field(default_factory=frozenset[Capability])


@dataclass(frozen=True)
class RegistryMatch:
    """Result of matching required capabilities against a registered agent.

    Attributes:
        agent: The matched agent profile.
        score: Match quality from 0.0 (nothing matched) to 1.0 (all matched
            at required level or above).
        matched_capabilities: Capabilities the agent satisfies.
        missing_capabilities: Capabilities the agent lacks or has at too low
            a level.
    """

    agent: AgentProfile
    score: float
    matched_capabilities: frozenset[Capability]
    missing_capabilities: frozenset[Capability]


class CapabilityRegistry:
    """Register agents with explicit capabilities and query by required skills.

    Usage::

        registry = CapabilityRegistry()
        registry.register("claude", "claude-opus-4-0520", frozenset({
            Capability("python", CapabilityLevel.EXPERT),
            Capability("testing", CapabilityLevel.EXPERT),
        }))
        matches = registry.find_agents([
            Capability("python", CapabilityLevel.ADVANCED),
        ])
    """

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentProfile] = {}

    # -- mutation ----------------------------------------------------------

    def register(
        self,
        adapter_name: str,
        model: str,
        capabilities: frozenset[Capability],
    ) -> AgentProfile:
        """Register (or replace) an agent's capability profile.

        Args:
            adapter_name: Adapter identifier.
            model: Model identifier.
            capabilities: Frozen set of capabilities.

        Returns:
            The stored ``AgentProfile``.
        """
        profile = AgentProfile(
            adapter_name=adapter_name,
            model=model,
            capabilities=capabilities,
        )
        self._agents[(adapter_name, model)] = profile
        logger.debug("registered %s/%s with %d capabilities", adapter_name, model, len(capabilities))
        return profile

    def unregister(self, adapter_name: str, model: str) -> bool:
        """Remove a previously registered agent.

        Returns:
            True if the agent was present and removed, False otherwise.
        """
        return self._agents.pop((adapter_name, model), None) is not None

    @property
    def agents(self) -> list[AgentProfile]:
        """All currently registered agents."""
        return list(self._agents.values())

    # -- query -------------------------------------------------------------

    def find_agents(
        self,
        required_capabilities: list[Capability],
        *,
        min_score: float = 0.0,
    ) -> list[RegistryMatch]:
        """Find agents matching the required capabilities, ranked by score.

        An agent's score is the fraction of required capabilities it
        satisfies *at the required level or higher*.  Results are returned
        in descending score order with ties broken by adapter name for
        determinism.

        Args:
            required_capabilities: Capabilities a task needs.
            min_score: Exclude agents below this score (0.0 -- 1.0).

        Returns:
            List of ``RegistryMatch`` sorted by score descending.
        """
        if not required_capabilities:
            return [
                RegistryMatch(
                    agent=profile,
                    score=1.0,
                    matched_capabilities=frozenset(),
                    missing_capabilities=frozenset(),
                )
                for profile in self._agents.values()
            ]

        results: list[RegistryMatch] = []
        for profile in self._agents.values():
            matched, missing = self._evaluate(profile, required_capabilities)
            score = len(matched) / len(required_capabilities)
            if score < min_score:
                continue
            results.append(
                RegistryMatch(
                    agent=profile,
                    score=round(score, 3),
                    matched_capabilities=frozenset(matched),
                    missing_capabilities=frozenset(missing),
                )
            )

        results.sort(key=lambda m: (-m.score, m.agent.adapter_name))
        return results

    def best_match(
        self,
        required_capabilities: list[Capability],
    ) -> RegistryMatch | None:
        """Return the single best-scoring agent, or ``None`` if the registry is empty.

        Args:
            required_capabilities: Capabilities a task needs.

        Returns:
            The highest-scored ``RegistryMatch``, or ``None``.
        """
        matches = self.find_agents(required_capabilities)
        return matches[0] if matches else None

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _evaluate(
        profile: AgentProfile,
        required: list[Capability],
    ) -> tuple[list[Capability], list[Capability]]:
        """Check which required capabilities a profile satisfies.

        A capability is satisfied when the agent has a capability with the
        same name whose level is >= the required level.
        """
        caps_by_name: dict[str, CapabilityLevel] = {c.name: c.level for c in profile.capabilities}
        matched: list[Capability] = []
        missing: list[Capability] = []
        for req in required:
            agent_level = caps_by_name.get(req.name)
            if agent_level is not None and agent_level >= req.level:
                matched.append(req)
            else:
                missing.append(req)
        return matched, missing


# ===================================================================
# Default capability profiles for well-known models
# ===================================================================

_ALL_CAPABILITIES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "frontend",
    "backend",
    "testing",
    "devops",
    "security",
    "refactoring",
    "code-review",
    "design",
    "documentation",
    "machine-learning",
    "long-context",
    "reasoning",
)

_HAIKU_BASIC_CAPS: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "frontend",
    "backend",
    "testing",
    "documentation",
)


def build_default_profiles() -> list[AgentProfile]:
    """Build default capability profiles for well-known Claude models.

    Returns:
        A list of ``AgentProfile`` instances for claude-opus, claude-sonnet,
        and claude-haiku with reasonable default capability levels.
    """
    opus_caps = frozenset(Capability(name=c, level=CapabilityLevel.EXPERT) for c in _ALL_CAPABILITIES)

    sonnet_advanced: frozenset[str] = frozenset(_ALL_CAPABILITIES) - {"long-context", "machine-learning", "design"}
    sonnet_caps = frozenset(
        Capability(
            name=c,
            level=CapabilityLevel.ADVANCED if c in sonnet_advanced else CapabilityLevel.BASIC,
        )
        for c in _ALL_CAPABILITIES
    )

    haiku_caps = frozenset(Capability(name=c, level=CapabilityLevel.BASIC) for c in _HAIKU_BASIC_CAPS)

    return [
        AgentProfile(adapter_name="claude", model="claude-opus-4-0520", capabilities=opus_caps),
        AgentProfile(adapter_name="claude", model="claude-sonnet-4-0520", capabilities=sonnet_caps),
        AgentProfile(adapter_name="claude", model="claude-haiku", capabilities=haiku_caps),
    ]


def populate_registry_defaults(registry: CapabilityRegistry) -> None:
    """Populate a registry with default profiles for well-known models.

    Convenience helper so callers don't need to know the details::

        registry = CapabilityRegistry()
        populate_registry_defaults(registry)
    """
    for profile in build_default_profiles():
        registry.register(profile.adapter_name, profile.model, profile.capabilities)


# Canonical capability names and their synonyms
_CAPABILITY_ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "jsx": "javascript",
    "react": "frontend",
    "vue": "frontend",
    "svelte": "frontend",
    "css": "frontend",
    "html": "frontend",
    "ui": "frontend",
    "api": "backend",
    "database": "backend",
    "sql": "backend",
    "db": "backend",
    "rest": "backend",
    "graphql": "backend",
    "test": "testing",
    "tests": "testing",
    "pytest": "testing",
    "jest": "testing",
    "unittest": "testing",
    "ci": "devops",
    "cd": "devops",
    "docker": "devops",
    "k8s": "devops",
    "kubernetes": "devops",
    "terraform": "devops",
    "infra": "devops",
    "infrastructure": "devops",
    "deploy": "devops",
    "deployment": "devops",
    "sec": "security",
    "auth": "security",
    "crypto": "security",
    "encryption": "security",
    "vulnerability": "security",
    "refactor": "refactoring",
    "cleanup": "refactoring",
    "restructure": "refactoring",
    "review": "code-review",
    "code_review": "code-review",
    "lint": "code-review",
    "architecture": "design",
    "architect": "design",
    "design-patterns": "design",
    "docs": "documentation",
    "readme": "documentation",
    "docstring": "documentation",
    "markdown": "documentation",
    "ml": "machine-learning",
    "ai": "machine-learning",
    "model-training": "machine-learning",
    "data-science": "machine-learning",
}

# Maps canonical capabilities to the agent best_for tags they match
_CAPABILITY_TO_BEST_FOR: dict[str, set[str]] = {
    "python": {"code-generation", "complex-refactoring", "code-modification"},
    "javascript": {"frontend", "full-stack", "code-generation"},
    "typescript": {"frontend", "full-stack", "code-generation"},
    "frontend": {"frontend", "full-stack", "multimodal"},
    "backend": {"code-generation", "full-stack", "complex-refactoring"},
    "testing": {"test-writing", "code-review", "quick-fixes"},
    "devops": {"automation", "headless-runs"},
    "security": {"security-review", "architecture", "tool-use"},
    "refactoring": {"complex-refactoring", "code-modification", "refactoring"},
    "code-review": {"code-review", "security-review"},
    "design": {"architecture", "tool-use", "complex-refactoring"},
    "documentation": {"frontend", "full-stack"},
    "machine-learning": {"code-generation", "reasoning-tasks"},
    "long-context": {"long-context"},
    "tool-use": {"tool-use"},
    "reasoning": {"reasoning-tasks"},
    "fast": {"quick-fixes", "fast-tasks"},
    "cheap": {"free-tier"},
    "headless": {"headless-runs"},
    "sandbox": set(),
    "mcp": set(),
}


def normalize_capability(cap: str) -> str:
    """Normalize a capability name to its canonical form."""
    cleaned = cap.strip().lower().replace(" ", "-").replace("_", "-")
    return _CAPABILITY_ALIASES.get(cleaned, cleaned)


def infer_capabilities_from_description(description: str) -> list[str]:
    """Infer required capabilities from a task description using keyword analysis."""
    text = description.lower()
    tokens = set(re.findall(r"\b\w+\b", text))
    inferred: set[str] = set()

    keyword_map: dict[str, list[str]] = {
        "python": ["python", "pytest", "pip", "pyright", "ruff", "mypy", "django", "flask", "fastapi"],
        "javascript": ["javascript", "node", "npm", "yarn", "webpack", "eslint"],
        "typescript": ["typescript", "tsx", "tsc"],
        "frontend": ["react", "vue", "svelte", "css", "html", "tailwind", "component", "ui", "ux"],
        "backend": ["api", "endpoint", "database", "sql", "migration", "server", "route", "handler"],
        "testing": ["test", "tests", "spec", "coverage", "assert", "mock", "fixture"],
        "devops": ["docker", "kubernetes", "ci", "cd", "pipeline", "deploy", "terraform", "ansible"],
        "security": ["security", "vulnerability", "auth", "oauth", "jwt", "encryption", "xss", "csrf"],
        "refactoring": ["refactor", "cleanup", "restructure", "rename", "extract", "simplify"],
        "code-review": ["review", "lint", "quality", "standards"],
        "design": ["architecture", "design", "pattern", "interface", "abstraction"],
        "documentation": ["docs", "readme", "document", "docstring", "changelog"],
        "machine-learning": ["model", "training", "inference", "embedding", "neural", "ml", "ai"],
    }

    for cap, keywords in keyword_map.items():
        if tokens & set(keywords):
            inferred.add(cap)

    return sorted(inferred)


@dataclass(frozen=True)
class CapabilityMatch:
    """Result of matching a task's required capabilities to an agent."""

    agent_name: str
    model: str
    match_score: float  # 0.0 to 1.0
    matched_capabilities: list[str]
    missing_capabilities: list[str]
    reason: str


@dataclass
class CapabilityRouter:
    """Routes tasks to agents based on required capabilities.

    Attributes:
        discovery: Cached agent discovery result.
        _agent_caps: Precomputed capability sets per agent.
    """

    discovery: DiscoveryResult
    _agent_caps: dict[str, set[str]] = field(default_factory=dict[str, set[str]], init=False, repr=False)

    def __post_init__(self) -> None:
        self._build_agent_capability_index()

    @staticmethod
    def _caps_for_agent(agent: object) -> set[str]:
        """Derive capabilities for a single agent based on its properties."""
        caps: set[str] = set()

        # Feature-based capabilities
        if getattr(agent, "supports_headless", False):
            caps.add("headless")
        if getattr(agent, "supports_sandbox", False):
            caps.add("sandbox")
        if getattr(agent, "supports_mcp", False):
            caps.update(("mcp", "tool-use"))

        # Reasoning strength
        reasoning = getattr(agent, "reasoning_strength", "")
        if reasoning in ("high", "very_high"):
            caps.update(("reasoning", "design", "security", "refactoring"))
        if reasoning == "very_high":
            caps.add("code-review")

        # Cost tier
        if getattr(agent, "cost_tier", "") in ("free", "cheap"):
            caps.update(("cheap", "fast"))

        # Context window
        if getattr(agent, "max_context_tokens", 0) >= 500_000:
            caps.add("long-context")

        # best_for tags → capabilities (reverse mapping)
        for bf_tag in getattr(agent, "best_for", ()):
            caps.add(bf_tag)
            for cap, bf_set in _CAPABILITY_TO_BEST_FOR.items():
                if bf_tag in bf_set:
                    caps.add(cap)

        # All agents can do basic coding
        caps.update(("python", "javascript", "typescript", "backend"))
        return caps

    def _build_agent_capability_index(self) -> None:
        """Build a capability set for each discovered agent."""
        for agent in self.discovery.agents:
            if not agent.logged_in:
                continue
            self._agent_caps[agent.name] = self._caps_for_agent(agent)

    def match(
        self,
        required: list[str],
        preferred_agent: str | None = None,
        min_score: float = 0.0,
    ) -> list[CapabilityMatch]:
        """Match required capabilities to available agents.

        Args:
            required: List of required capability names (will be normalized).
            preferred_agent: Optional agent name to boost in ranking.
            min_score: Minimum match score to include (0.0 to 1.0).

        Returns:
            List of CapabilityMatch sorted by score descending.
        """
        normalized = [normalize_capability(c) for c in required]
        if not normalized:
            return self._all_agents_default()

        matches: list[CapabilityMatch] = []
        for agent in self.discovery.agents:
            if not agent.logged_in:
                continue
            agent_caps = self._agent_caps.get(agent.name, set())
            matched = [c for c in normalized if c in agent_caps]
            missing = [c for c in normalized if c not in agent_caps]

            score = len(matched) / len(normalized) if normalized else 0.0

            # Boost preferred agent slightly
            if preferred_agent and agent.name == preferred_agent:
                score = min(1.0, score + 0.1)

            if score < min_score:
                continue

            # Pick best model for the required capabilities
            model = self._select_model_for_caps(agent, normalized)

            reason = self._build_reason(agent, matched, missing)
            matches.append(
                CapabilityMatch(
                    agent_name=agent.name,
                    model=model,
                    match_score=round(score, 3),
                    matched_capabilities=matched,
                    missing_capabilities=missing,
                    reason=reason,
                )
            )

        matches.sort(key=lambda m: m.match_score, reverse=True)
        return matches

    def best_match(
        self,
        required: list[str],
        preferred_agent: str | None = None,
    ) -> CapabilityMatch | None:
        """Return the single best matching agent, or None if no match."""
        matches = self.match(required, preferred_agent=preferred_agent)
        return matches[0] if matches else None

    def _select_model_for_caps(self, agent: AgentCapabilities, caps: list[str]) -> str:
        """Pick the best model on this agent for the required capabilities."""
        needs_strong = any(c in ("design", "security", "refactoring", "code-review", "reasoning") for c in caps)
        needs_cheap = any(c in ("cheap", "fast") for c in caps)
        needs_long_ctx = "long-context" in caps

        if needs_long_ctx and agent.max_context_tokens >= 500_000:
            return agent.default_model

        if needs_strong and len(agent.available_models) > 1:
            # Prefer the strongest model
            return agent.available_models[0]

        if needs_cheap and len(agent.available_models) > 1:
            # Prefer the cheapest (usually last in list or has "mini"/"flash"/"haiku")
            for m in reversed(agent.available_models):
                if any(tag in m.lower() for tag in ("mini", "flash", "haiku", "turbo", "small")):
                    return m
            return agent.available_models[-1]

        return agent.default_model

    def _all_agents_default(self) -> list[CapabilityMatch]:
        """When no capabilities specified, return all agents with default score."""
        return [
            CapabilityMatch(
                agent_name=agent.name,
                model=agent.default_model,
                match_score=0.5,
                matched_capabilities=[],
                missing_capabilities=[],
                reason="no capabilities specified, any agent can handle",
            )
            for agent in self.discovery.agents
            if agent.logged_in
        ]

    @staticmethod
    def _build_reason(
        agent: AgentCapabilities,
        matched: list[str],
        missing: list[str],
    ) -> str:
        parts: list[str] = []
        if matched:
            parts.append(f"matches: {', '.join(matched[:3])}")
        if missing:
            parts.append(f"missing: {', '.join(missing[:3])}")
        if agent.cost_tier == "free":
            parts.append("free tier")
        return "; ".join(parts) if parts else "available"
