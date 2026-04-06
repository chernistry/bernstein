"""CLAUDE-006: Prompt caching optimization based on shared context analysis.

Identifies cacheable prompt segments by analyzing which parts of the
system prompt are shared across agents.  Groups agents by common prefixes
(role templates, project context, constraints) and recommends caching
boundaries that maximize cache hit rates.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Minimum token count for a segment to be worth caching.
# Anthropic's prompt caching requires at least 1024 tokens for the prefix.
MIN_CACHEABLE_TOKENS: int = 1024

# Approximate chars per token for estimation.
_CHARS_PER_TOKEN: int = 4


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length.

    Args:
        text: Input text.

    Returns:
        Estimated token count (minimum 1).
    """
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _hash_text(text: str) -> str:
    """Compute SHA-256 hash of text for cache key.

    Args:
        text: Text to hash.

    Returns:
        Hex digest of the hash.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheableSegment:
    """A prompt segment identified as cacheable.

    Attributes:
        segment_id: Hash-based identifier for the segment.
        content: The text content of the segment.
        token_estimate: Estimated token count.
        shared_by: Set of agent roles that share this segment.
        cache_type: "system" for system prompts, "context" for project context.
        savings_per_hit_usd: Estimated cost savings per cache hit.
    """

    segment_id: str
    content: str
    token_estimate: int
    shared_by: frozenset[str]
    cache_type: str
    savings_per_hit_usd: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "segment_id": self.segment_id,
            "token_estimate": self.token_estimate,
            "shared_by": sorted(self.shared_by),
            "cache_type": self.cache_type,
            "savings_per_hit_usd": round(self.savings_per_hit_usd, 6),
        }


@dataclass(frozen=True, slots=True)
class CacheOptimizationPlan:
    """Result of prompt cache analysis.

    Attributes:
        segments: Identified cacheable segments.
        total_cacheable_tokens: Sum of tokens across all cacheable segments.
        estimated_savings_per_run_usd: Projected savings if all segments hit cache.
        cache_hit_rate: Estimated cache hit rate (0.0-1.0).
    """

    segments: list[CacheableSegment]
    total_cacheable_tokens: int
    estimated_savings_per_run_usd: float
    cache_hit_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "segments": [s.to_dict() for s in self.segments],
            "total_cacheable_tokens": self.total_cacheable_tokens,
            "estimated_savings_per_run_usd": round(self.estimated_savings_per_run_usd, 6),
            "cache_hit_rate": round(self.cache_hit_rate, 3),
        }


# Anthropic cached input discount: $0.30/MTok vs $3.00/MTok standard.
# Savings per million cached tokens = $3.00 - $0.30 = $2.70.
_SAVINGS_PER_MTOK: float = 2.70


@dataclass
class PromptCacheOptimizer:
    """Analyzes agent prompts to identify caching opportunities.

    Collects prompt segments from multiple agents, finds shared prefixes,
    and recommends cache boundaries.

    Attributes:
        agent_prompts: Mapping from agent role to their system prompt parts.
        segments: Discovered cacheable segments.
    """

    agent_prompts: dict[str, list[str]] = field(default_factory=dict)
    segments: list[CacheableSegment] = field(default_factory=list)

    def add_agent_prompt(self, role: str, parts: list[str]) -> None:
        """Register prompt parts for an agent role.

        Args:
            role: Agent role name (e.g. "backend", "qa").
            parts: Ordered list of prompt segments (role template, context, etc.).
        """
        self.agent_prompts[role] = parts

    def analyze(self) -> CacheOptimizationPlan:
        """Analyze all registered prompts and identify cacheable segments.

        Groups segments that are shared across multiple agents and estimates
        caching savings.

        Returns:
            CacheOptimizationPlan with recommendations.
        """
        # Build a map from segment content hash -> (content, set of roles).
        content_map: dict[str, tuple[str, set[str]]] = {}

        for role, parts in self.agent_prompts.items():
            for part in parts:
                h = _hash_text(part)
                if h in content_map:
                    content_map[h][1].add(role)
                else:
                    content_map[h] = (part, {role})

        # Filter to segments worth caching.
        self.segments = []
        for h, (content, roles) in content_map.items():
            tokens = _estimate_tokens(content)
            if tokens < MIN_CACHEABLE_TOKENS:
                continue

            savings = (tokens / 1_000_000) * _SAVINGS_PER_MTOK * max(len(roles) - 1, 0)
            cache_type = "system" if len(roles) > 1 else "context"

            self.segments.append(
                CacheableSegment(
                    segment_id=h[:16],
                    content=content,
                    token_estimate=tokens,
                    shared_by=frozenset(roles),
                    cache_type=cache_type,
                    savings_per_hit_usd=savings,
                )
            )

        # Sort by savings (highest first).
        self.segments.sort(key=lambda s: s.savings_per_hit_usd, reverse=True)

        total_tokens = sum(s.token_estimate for s in self.segments)
        total_savings = sum(s.savings_per_hit_usd for s in self.segments)

        # Estimate hit rate: shared segments have higher hit probability.
        if self.segments:
            shared_tokens = sum(s.token_estimate for s in self.segments if len(s.shared_by) > 1)
            hit_rate = shared_tokens / total_tokens if total_tokens > 0 else 0.0
        else:
            hit_rate = 0.0

        plan = CacheOptimizationPlan(
            segments=self.segments,
            total_cacheable_tokens=total_tokens,
            estimated_savings_per_run_usd=total_savings,
            cache_hit_rate=hit_rate,
        )

        logger.info(
            "Cache optimization: %d segments, %d tokens, $%.4f estimated savings",
            len(self.segments),
            total_tokens,
            total_savings,
        )

        return plan

    def recommend_prefix_order(self) -> list[str]:
        """Recommend optimal ordering of prompt parts for cache hits.

        Shared segments should come first (as prefixes) so that
        different agents can share the same cache entry.

        Returns:
            Ordered list of segment IDs from most-shared to least-shared.
        """
        sorted_segments = sorted(
            self.segments,
            key=lambda s: (len(s.shared_by), s.token_estimate),
            reverse=True,
        )
        return [s.segment_id for s in sorted_segments]
