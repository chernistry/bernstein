"""Integration tests: lethal-trifecta evaluator inside the policy stack.

These verify that ``evaluate_lethal_trifecta`` produces a bypass-immune
IMMUNE decision so plugins running with ``bypass_enabled=True`` cannot
override the structural rule.
"""

from __future__ import annotations

import pytest

from bernstein.core.security.capability_matrix import (
    Capability,
    CapabilityRegistry,
    EnforcementMode,
    ToolCapabilities,
)
from bernstein.core.security.policy_engine import (
    DecisionGraph,
    DecisionType,
    PermissionDecision,
    evaluate_lethal_trifecta,
)


@pytest.fixture()
def trifecta_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(
        ToolCapabilities(
            tool_name="fs.read_secret",
            capabilities=frozenset({Capability.PRIVATE_DATA}),
        )
    )
    reg.register(
        ToolCapabilities(
            tool_name="github.fetch_issue",
            capabilities=frozenset({Capability.UNTRUSTED_INPUT, Capability.EXTERNAL_COMM}),
        )
    )
    reg.register(
        ToolCapabilities(
            tool_name="github.post_comment",
            capabilities=frozenset({Capability.EXTERNAL_COMM}),
        )
    )
    return reg


class TestEvaluateLethalTrifecta:
    def test_safe_chain_returns_allow(self, trifecta_registry: CapabilityRegistry) -> None:
        decision = evaluate_lethal_trifecta(["fs.read_secret"], trifecta_registry)
        assert decision.type == DecisionType.ALLOW

    def test_full_trifecta_returns_immune_with_bypass_immune(self, trifecta_registry: CapabilityRegistry) -> None:
        decision = evaluate_lethal_trifecta(
            ["fs.read_secret", "github.fetch_issue", "github.post_comment"],
            trifecta_registry,
        )
        assert decision.type == DecisionType.IMMUNE
        assert decision.bypass_immune is True
        assert "lethal trifecta" in decision.reason

    def test_warn_mode_returns_allow(self, trifecta_registry: CapabilityRegistry) -> None:
        trifecta_registry.mode = EnforcementMode.WARN
        decision = evaluate_lethal_trifecta(
            ["fs.read_secret", "github.fetch_issue", "github.post_comment"],
            trifecta_registry,
        )
        assert decision.type == DecisionType.ALLOW


class TestDecisionGraphIntegration:
    def test_immune_layer_blocks_even_with_bypass_enabled(self, trifecta_registry: CapabilityRegistry) -> None:
        graph = DecisionGraph(bypass_enabled=True)
        graph.add_decision(
            evaluate_lethal_trifecta(
                ["fs.read_secret", "github.fetch_issue", "github.post_comment"],
                trifecta_registry,
            )
        )
        graph.add_decision(PermissionDecision(DecisionType.ALLOW, "plugin allowed"))
        final = graph.evaluate()
        assert final.type == DecisionType.IMMUNE
        assert "lethal trifecta" in final.reason

    def test_safe_chain_yields_allow_through_graph(self, trifecta_registry: CapabilityRegistry) -> None:
        graph = DecisionGraph(bypass_enabled=False)
        graph.add_decision(evaluate_lethal_trifecta(["fs.read_secret"], trifecta_registry))
        final = graph.evaluate()
        assert final.type == DecisionType.ALLOW

    def test_unknown_tool_default_deny_stays_immune_with_bypass(self, trifecta_registry: CapabilityRegistry) -> None:
        graph = DecisionGraph(bypass_enabled=True)
        graph.add_decision(evaluate_lethal_trifecta(["mystery.tool"], trifecta_registry))
        final = graph.evaluate()
        assert final.type == DecisionType.IMMUNE
        assert final.bypass_immune is True
